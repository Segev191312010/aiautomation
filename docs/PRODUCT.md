# TradeBot Product Boundary

Status: development and paper/simulation use only

TradeBot is a local, single-operator trading workstation. The canonical product is the React dashboard backed by the Python trading runtime; a packaged Windows desktop shell is planned for Phase D.

## Supported now

- Simulation and development workflows.
- Authenticated local dashboard contracts documented by the Phase B OpenAPI snapshot.
- Qullamaggie- and Minervini-inspired technical screeners.

## Explicitly unavailable

- O'Neil/CANSLIM screening, because a validated fundamental-data feed is not integrated.
- Leading-industry ranking, because its calculation contract is not implemented.
- Public or remote web deployment.
- Unattended live-money release.

Unavailable workflows must be labeled in the UI and must not silently return fabricated or mock production results.
