# Chart feed contract

`ChartsPage` single-symbol mode uses TradingView's hosted Advanced Chart widget. The URL is built in `dashboard/src/utils/tradingView.ts`; symbol and interval are query parameters and the origin is supplied by the build-time `VITE_TRADINGVIEW_WIDGET_URL` setting (default: `https://www.tradingview.com/widgetembed/`). Multi-symbol mode remains the local `ib_chart` sidecar until a supported multi-chart TradingView integration is selected.

The widget is a presentation/embed surface, not a license grant. A TradingView Pro account does not automatically provide real-time exchange data to this application. Real-time versus delayed behavior depends on the account, exchange entitlements, feed licensing, widget terms, and the source serving the data. The UI must not promise zero delay until those entitlements are verified in the deployed environment.

The embed contract is covered by `dashboard/src/utils/__tests__/tradingView.test.ts`. It verifies timeframe mapping, symbol normalization, URL encoding, and the trusted default origin. A manual browser check is still required for CSP, widget load, and entitlement behavior.
