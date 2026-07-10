# Web GUI

**🌍 Language / Lingua:** [🇬🇧 English](../../docs/en/gui.md) | [🇮🇹 Italiano](gui.md)

<- [Torna al README principale](../../docs/it/README.md)

Interfaccia web basata su Django per la ricerca e il download di contenuti direttamente dal browser.

![Home](../img/gui/home.png)

---

## Avvio rapido

```bash
pip install -r GUI/requirements.txt
python GUI/manage.py migrate
python GUI/manage.py runserver 0.0.0.0:8000
```

Poi apri `http://<host>:8000` nel browser. Per i deploy Docker/NAS consulta il README
principale e la [guida al deployment su NAS](NAS.md) invece di avviare direttamente il server
di sviluppo.

---

## Funzionalità

### Ricerca e download

- **Home** (`/`) — scegli un sito e cerca un titolo.
- **Risultati** (`/search/`) — ogni risultato può essere scaricato, aggiunto alla watchlist
  o (per le serie) espanso per mostrare stagioni ed episodi tramite la vista di dettaglio.
- **Avvio download** (`/download/`) — mette in coda il film o gli episodi selezionati. La
  selezione delle tracce (video/audio/sottotitoli) segue gli stessi filtri di `config.json`
  usati dalla CLI.

### Dashboard dei download

`/downloads/` mostra la coda dei download in tempo reale e la cronologia:

- Stato e avanzamento live via `api/get-downloads/`.
- **Interrompi** un download in corso (`api/kill-download/`).
- **Interrompi e svuota la coda** (`api/kill-and-clear-queue/`).
- **Pulisci la cronologia** delle voci completate/fallite (`api/clear-history/`).

> Per alcuni provider la barra di avanzamento può restare a 0% anche se il download è in
> corso — vedi *Problemi noti* nel README principale.

### Watchlist e download automatico

`/watchlist/` tiene traccia di serie (e film) e può scaricare i nuovi contenuti in automatico:

- **Aggiungi** un titolo dai risultati di ricerca, oppure **rimuovi** i singoli elementi o
  **svuota** l'intera lista. I metadati (stagioni, poster, TMDB id) vengono recuperati in
  background per mantenere reattiva l'interfaccia.
- **Aggiorna tutto** ricontrolla ogni elemento su richiesta.
- **Download automatico per elemento** (`watchlist/auto/<id>`): per una serie lo abiliti su
  una stagione specifica; VibraVid scarica poi automaticamente i nuovi episodi pubblicati di
  quella stagione.
- **Esegui ora** (`watchlist/auto-run/`) avvia un controllo immediato senza attendere il ciclo
  successivo.
- **Intervallo di polling** (`watchlist/auto-interval/`) — ogni quanto il loop automatico
  controlla i nuovi episodi. Il valore predefinito è **4 ore** (14400 s); i valori
  selezionabili sono 5 min, 15 min, 30 min, 1 h, 6 h, 12 h e 24 h. L'intervallo può essere
  impostato anche con la variabile d'ambiente `WATCHLIST_AUTO_INTERVAL_SECONDS`.

### Editor impostazioni / configurazione

`/settings/` è un editor nel browser per `Conf/config.json` e `Conf/login.json`:

- Modifica entrambi i file in schede, valida il JSON prima di salvare e scrive un `.backup`
  accanto all'originale.
- `ARR.max_concurrent_downloads` viene applicato a caldo senza riavvio. La maggior parte delle
  altre impostazioni ha effetto dopo un **reload** (`api/reload-config/`, che ricarica config
  e/o login tramite il config manager) o un riavvio del server.

### Upload di servizi custom

Carica un modulo sito personalizzato come ZIP (`api/upload-service/`); viene estratto in
`VibraVid/services/` e il registro viene ricaricato (`api/registry-status/`). Complementa la
chiave `imp_service` del config. Un servizio caricato compare nel menu a tendina dei siti solo
se include uno stub corrispondente in `GUI/searchapp/api/<nome_servizio>.py`.

### Aggiornamento in-app

Quando è disponibile una nuova release, l'interfaccia mostra un banner di aggiornamento. Il
controllo versione (`api/version/check/`) è memorizzato in cache per un'ora; l'azione di
aggiornamento (`api/version/update/`) lo applica in place. Per l'aggiornamento one-click su
Docker (requisito del Docker socket) vedi il README principale.

### Pagina ARR stack

`/arr-stack/` è un pannello di stato e controllo per l'integrazione Seerr/Sonarr/Radarr:
elenca la coda interna di elaborazione ARR di VibraVid (filtrabile per stato/sorgente/sync) e
può avviare una sincronizzazione (`api/arr/trigger-sync/`). Gli endpoint webhook e la
configurazione completa sono documentati nella
[sezione ARR del README principale](../../docs/it/README.md#integrazione-arr).

---

## CSRF & Reverse Proxy

Quando si accede alla GUI dall'esterno della rete locale o dietro un reverse proxy, Django potrebbe rifiutare le richieste a causa della validazione CSRF. Configurare le seguenti variabili d'ambiente in base alla propria configurazione.

### Origini attendibili

Necessario quando le richieste provengono da un dominio o porta diversi da quelli attesi da Django:

```
CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000 https://tuodominio.it"
```

### Forwarding HTTPS

Se il reverse proxy termina SSL/TLS, è necessario inoltrare lo schema a Django:

**Apache:**
```apache
RequestHeader set X-Forwarded-Proto "https"
```

**Variabile d'ambiente:**
```
SECURE_PROXY_SSL_HEADER_ENABLED=true
```

### Variabili consigliate per deploy dietro proxy

```
ALLOWED_HOSTS="streaming.tuodominio.it"
USE_X_FORWARDED_HOST=true
CSRF_COOKIE_SECURE=true
SESSION_COOKIE_SECURE=true
```
