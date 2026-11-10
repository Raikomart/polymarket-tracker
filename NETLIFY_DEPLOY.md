# Netlify deployment

This fork now includes a small Netlify-ready public wallet tracker in `public/index.html` and a server-side function in `netlify/functions/polymarket.mjs`. The existing Flask app and its files are left in place.

## Deploy from GitHub

1. Push or merge the `live-wallet-dashboard` branch in this fork. You can preview this branch directly, or merge it into `main` when ready.
2. In Netlify, choose **Add new site → Import an existing project** and connect `Raikomart/polymarket-tracker`.
3. Set the production branch to `live-wallet-dashboard` (or `main` after merging).
4. Netlify should read `netlify.toml`. Verify the publish directory is `public`; no build command or API key is required.
5. Click **Deploy site**. Netlify builds the function automatically.
6. Open the deployed site, paste a public `0x` wallet address, and choose **View portfolio**.

## Local preview

Install the Netlify CLI and run:

```sh
npm install -g netlify-cli
netlify dev
```

Open the local URL printed by the CLI. `netlify dev` serves the static page and the server-side function together. Opening `public/index.html` directly as a file will not run the function.

## Data and safety

The function reads public wallet positions from Polymarket's Data API and requests sell-side quotes from the public CLOB API. The browser only calls this site's Netlify Function, avoiding browser-to-Polymarket CORS issues. No private key, seed phrase, login, or API key is requested or stored. The wallet address is only kept in the page while it is open; it is not saved to local storage.

The tracker refreshes every 15 seconds while an address is entered. Market quotes can be unavailable for an illiquid/closed token; in that case the displayed position mark falls back to the price returned by the positions API.
