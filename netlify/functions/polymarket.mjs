export const handler = async (event) => {
  const address = (event.queryStringParameters?.address || "").trim();
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  };

  const reply = (statusCode, body) => ({
    statusCode,
    headers,
    body: JSON.stringify(body),
  });

  if (!/^0x[a-fA-F0-9]{40}$/.test(address)) {
    return reply(400, {
      error: "Enter a valid public wallet address (0x followed by 40 hexadecimal characters).",
    });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);

  try {
    const positionsUrl = new URL("https://data-api.polymarket.com/positions");
    positionsUrl.searchParams.set("user", address);
    positionsUrl.searchParams.set("limit", "500");

    const response = await fetch(positionsUrl, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });

    if (!response.ok) {
      console.error("Polymarket positions request failed:", response.status);
      return reply(502, {
        error: "Polymarket could not return positions right now. Please try again shortly.",
      });
    }

    const payload = await response.json();
    const sourcePositions = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.data)
        ? payload.data
        : Array.isArray(payload?.positions)
          ? payload.positions
          : null;

    if (!sourcePositions) {
      console.error("Unexpected Polymarket positions response format");
      return reply(502, {
        error: "Polymarket returned an unexpected response. Please try again shortly.",
      });
    }

    // Keep requests bounded. A sell-side quote is useful for estimating what a
    // position could currently realize; the position API's curPrice is fallback.
    const positions = sourcePositions.slice(0, 100);
    let nextIndex = 0;
    const worker = async () => {
      while (nextIndex < positions.length) {
        const index = nextIndex++;
        const position = positions[index];
        const tokenId = String(position.asset || position.token_id || position.tokenId || "");
        let quote = null;

        if (tokenId) {
          try {
            const quoteUrl = new URL("https://clob.polymarket.com/price");
            quoteUrl.searchParams.set("token_id", tokenId);
            quoteUrl.searchParams.set("side", "sell");
            const quoteResponse = await fetch(quoteUrl, {
              headers: { Accept: "application/json" },
              signal: controller.signal,
            });
            if (quoteResponse.ok) {
              const quoteData = await quoteResponse.json();
              const parsed = Number(quoteData.price);
              if (Number.isFinite(parsed)) quote = parsed;
            }
          } catch (error) {
            if (controller.signal.aborted) throw error;
            // Per-market quote failure falls back to Polymarket's position mark.
          }
        }

        const size = Number(position.size ?? position.current_size ?? 0) || 0;
        const apiPrice = Number(position.curPrice ?? position.current_price ?? position.avgPrice ?? position.avg_price);
        const currentPrice = quote ?? (Number.isFinite(apiPrice) ? apiPrice : null);
        const apiValue = Number(position.currentValue ?? position.current_value);
        const currentValue = Number.isFinite(apiValue)
          ? apiValue
          : currentPrice === null
            ? null
            : size * currentPrice;
        const initialValue = Number(position.initialValue ?? position.entry_cost_usdc ?? position.initial_value);
        const apiPnl = Number(position.cashPnl ?? position.unrealized_pnl ?? position.unrealizedPnl);
        const pnl = Number.isFinite(apiPnl)
          ? apiPnl
          : Number.isFinite(initialValue) && currentValue !== null
            ? currentValue - initialValue
            : null;

        positions[index] = {
          title: String(position.title || position.question || "Polymarket position"),
          outcome: String(position.outcome || "—"),
          size,
          averagePrice: Number(position.avgPrice ?? position.avg_price) || 0,
          currentPrice,
          currentValue,
          pnl,
          realizedPnl: Number(position.realizedPnl ?? position.realized_pnl) || 0,
          status: position.redeemable ? "Redeemable" : "Open",
          tokenId,
        };
      }
    };

    await Promise.all(Array.from({ length: Math.min(5, positions.length) }, worker));

    return reply(200, {
      address,
      positions,
      truncated: sourcePositions.length > positions.length,
      lastUpdated: new Date().toISOString(),
    });
  } catch (error) {
    if (controller.signal.aborted) {
      return reply(504, { error: "Polymarket took too long to respond. Please try again." });
    }
    console.error("Wallet data request failed:", error);
    return reply(502, {
      error: "Could not load live Polymarket data. Check your connection and try again.",
    });
  } finally {
    clearTimeout(timeout);
  }
};
