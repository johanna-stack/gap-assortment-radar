# GAP Assortment Radar - Streamlit

Interaktiv dashboard för CDON:s GAP Assortment Radar (Trail). Visar uppstickande brands per
marknad som saknas i CDON:s sortiment, och vilken befintlig merchant som kan lägga upp dem.

## Vyer
- **Per marknad** - jobba en marknad i taget, sätt status (GAP / Kontaktad / Live) per marknad.
- **Per merchant** - samlar alla brands/marknader per merchant så du kontaktar en gång.
- Sök merchant, exportera CSV (komma/UTF-8, importeras rent i Google Sheets).

## Datakälla
Appen läser och skriver mot Aurora-backend via `AURORA_URL`:
- `GET /api/gap-radar` - findings + delad status
- `POST /api/gap-radar/state` - status/kommentar (loggas i audit)

Om `AURORA_URL` inte är nåbar faller appen tillbaka på den bundlade `findings.json`
(**read-only** - status sparas inte). Det gör att Cloud-deployen visar data direkt; sätt
`AURORA_URL` till en nåbar backend för full läs/skriv.

## Deploy på Streamlit Community Cloud
1. New app -> välj repot `johanna-stack/gap-assortment-radar`, branch `main`, main file `streamlit_app.py`.
2. Advanced settings -> Secrets:
   ```toml
   AURORA_URL = "https://<din-nåbara-aurora-backend>"
   ```
   Lämna bort `AURORA_URL` (eller sätt en onåbar) för read-only-läge mot bundlad findings.json.
3. Deploy.

## Kör lokalt
```bash
pip install -r requirements.txt
export AURORA_URL=http://127.0.0.1:5174
streamlit run streamlit_app.py
```
