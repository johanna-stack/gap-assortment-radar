# GAP Assortment Radar - Streamlit

Interaktiv dashboard för CDON:s GAP Assortment Radar (Trail). Uppstickande brands per marknad
som saknas i CDON:s sortiment, och vilken befintlig merchant som kan lägga upp dem.

## Vyer
- **Per marknad** - jobba en marknad i taget, sätt status (GAP / Kontaktad / Live) per marknad.
- **Per merchant** - samlar alla brands/marknader per merchant så du kontaktar en gång.
- Sök merchant, exportera CSV (komma/UTF-8, importeras rent i Google Sheets).

## Data (repo-backad)
- `findings.json` - gaps (brands/marknader). Pushas av Aurora efter varje scan.
- `state.json` - status + kommentarer. Skrivs av appen via GitHub Contents-API:t.

Appen läser båda från detta repo och skriver `state.json` tillbaka med en GitHub-token.
Ingen extern backend behövs. **Token (`GAP_RADAR_PAT`) är scopad till BARA detta repo -
cdon-trackers rörs aldrig.**

## Deploy på Streamlit Community Cloud
1. New app -> repo `johanna-stack/gap-assortment-radar`, branch `main`, main file `streamlit_app.py`.
2. Advanced settings -> Secrets:
   ```toml
   GAP_RADAR_PAT = "github_pat_..."
   ```
   (fine-grained PAT, Contents: Read and write, endast detta repo)
3. Deploy. Utan secret körs appen read-only (visar findings, sparar inte status).

## Kör lokalt
```bash
pip install -r requirements.txt
export GAP_RADAR_PAT=github_pat_...
streamlit run streamlit_app.py
```
