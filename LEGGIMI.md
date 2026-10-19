# Schermo Esteso

Usa l'iPad (6ª generazione, iPadOS 17), l'iPhone o altri dispositivi come **schermi aggiuntivi** del PC,
via Wi-Fi (fino a 4 dispositivi insieme, ognuno con il suo schermo).

## Uso quotidiano

1. Sul PC apri **Schermo Esteso** dal menu Start: si apre la finestra di controllo (console a
   sinistra; prestazioni di PC, scheda grafica e dispositivi a destra, riducibili con un clic sul
   titolo; file condivisi). In alto c'è l'indirizzo da aprire sul dispositivo.
2. Sul dispositivo apri in Safari l'indirizzo **https://…:8770**. La prima volta:
   - Safari mostra un avviso di sicurezza: *Mostra dettagli → visita questo sito web*;
   - installa il certificato del PC (menu → File → *Connessione sicura*): poi l'avviso sparisce,
     la connessione è cifrata e funziona il pulsante Condividi. Il certificato vale solo per la
     rete locale: non può essere usato per falsificare altri siti;
   - inserisci il **PIN** di 6 cifre che compare in grande nella finestra del PC.
3. *Condividi → Aggiungi alla schermata Home* per averla a tutto schermo come un'app.
4. Per chiudere tutto chiudi la finestra sul PC: gli schermi virtuali si spengono e le loro
   finestre tornano sul monitor principale.

Per spostare le finestre sull'iPad: trascinale oltre il bordo dello schermo del PC dalla parte
dove si trova lo schermo dell'iPad (*Impostazioni → Sistema → Schermo*).

## Sicurezza

- Connessione cifrata (HTTPS, TLS 1.2 o superiore) con un certificato creato sul PC (`certs/`).
- PIN diverso per ogni dispositivo nuovo; al massimo 3 richieste di abbinamento aperte; dopo 20
  PIN sbagliati gli abbinamenti si bloccano per 10 minuti.
- La finestra di controllo risponde solo a sé stessa (porta locale 8769): le pagine web aperte
  sul PC non possono leggerla né comandarla.
- *Apri qui* apre sul PC solo documenti e file multimediali, mai programmi o script. I file
  arrivati dai dispositivi sono marcati come "scaricati da Internet" (SmartScreen, Office).

- Firewall (`strumentiirewall.ps1`, da amministratore): la porta 8770 è aperta solo sulle reti
  **Private** e solo ai dispositivi della stessa rete locale. Su una rete nuova (es. l'hotspot del
  telefono) impostala come privata: *Impostazioni → Rete e Internet → Wi-Fi → nome rete → Rete
  privata*, altrimenti i dispositivi non si collegano. Sulle reti pubbliche il PC resta chiuso.

## Più dispositivi e chiusura dell'app

- Ogni dispositivo ha il suo schermo virtuale, sempre lo stesso: Windows ricorda per ognuno
  posizione, risoluzione e ridimensionamento.
- Lo schermo di un dispositivo si accende quando apri l'app e si spegne **15 secondi dopo** che
  l'hai chiusa (o che il dispositivo è andato in standby). Le sue finestre passano sul monitor
  principale, **sotto** quelle già aperte, così non coprono quello che stai facendo.
- Gli altri dispositivi continuano a funzionare: al massimo il video si ferma per meno di un secondo.
- Quando abbini un dispositivo **nuovo**, il driver viene riavviato una volta: gli schermi virtuali
  già accesi si spengono per qualche secondo e poi tornano al loro posto.
- Finché un dispositivo è collegato, il PC non va in standby e non spegne gli schermi.
- Quando chiudi la finestra del server, tutte le finestre tornano sul monitor principale.
- Elenco dei dispositivi abbinati: `dispositivi.json`. Per dimenticarli tutti, cancellalo.
Per cambiare posizione o risoluzione: *Impostazioni Windows → Sistema → Schermo*
(l'iPad è lo schermo "2", risoluzione consigliata 2048×1536 con ridimensionamento 200%).

## Scambio di file

Tutti i file stanno nella cartella **`download`** dell'app (`D:\claudio\ipad-schermo\download`).
Ogni file è visibile a **tutti** i dispositivi, anche a quelli che si collegheranno dopo.

- **Dal PC:** trascinali nella finestra di Schermo Esteso (sezione *File condivisi*), oppure
  copiali nella cartella con Esplora file, oppure clic destro → **Invia a** → **Schermo Esteso**
  (su Windows 11 è sotto *Mostra altre opzioni*). Doppio clic su un file nella finestra lo apre.
- **Dal dispositivo:** menu → **File** → *Aggiungi file*, oppure trascinali nel pannello.
- Quando arriva un file nuovo, i dispositivi collegati ricevono un avviso con *Apri*.
- Nel pannello **File**: tocca un file per l'**anteprima** (con *Chiudi*); l'icona di **condivisione**
  apre il menu di iPadOS (Salva su File, Foto, AirDrop…); *Apri qui* lo apre sul PC su questo schermo.
- I file si tolgono **solo cancellandoli dalla cartella sul PC**: dai dispositivi non si eliminano.

## Apple Pencil

La Pencil è una **penna vera di Windows** (Windows Ink): pressione e inclinazione arrivano ai
programmi. Nel menu, sezione *Apple Pencil*:
- **Automatica** (consigliata): sopra un programma da disegno o scrittura (Paint, OneNote,
  Lavagna, Word, PowerPoint, Acrobat, Krita, un PDF aperto nel browser…) la Pencil scrive; sopra
  tutto il resto fa da **dito**: trascinando scorri le pagine, toccando fai clic. Per aggiungere
  un programma all'elenco scrivi il nome del suo .exe (uno per riga) in `programmi-penna.txt`.
- **Penna**: scrive sempre (sul desktop o in una pagina web seleziona e trascina, non disegna).
- **Gomma**: come girare dal lato gomma una penna per PC. Cancella i tratti solo nei programmi
  che gestiscono la gomma (OneNote, Lavagna, Word, Paint, PDF in Edge); altrove non fa niente.
- **Mouse**: la Pencil fa da mouse preciso.

Mentre scrivi con la Pencil, i tocchi del palmo appoggiato vengono ignorati. In Windows la penna
lascia il segno solo nei programmi fatti per l'inchiostro.

## App aperte dallo Start

Se apri lo Start sullo schermo dell'iPad (o su un altro schermo) e lanci un'app, la sua finestra
compare su quello schermo invece che sul monitor principale.

Cosa ci si può fare: firmare o annotare PDF in Edge (*Disegna*), disegnare in Paint, Photoshop o
Krita, scrivere a mano in OneNote o Whiteboard, scrivere a mano nelle caselle di testo (Windows
propone il pannello di scrittura).

**Firmare un PDF:** metti il PDF nella cartella `download` (o trascinalo nella finestra del PC),
poi sull'iPad: menu → **File** → **Apri qui** sul PDF. Si apre in Edge direttamente sullo schermo
dell'iPad: scegli *Disegna*, firma con la Pencil e salva (Ctrl+S o l'icona del dischetto).
*Apri qui* funziona con qualsiasi file: si apre con il programma che Windows usa per quel tipo.
I file che si aprono con Edge usano il profilo separato dell'app, senza account, così non
finiscono tra le schede del tuo Edge.

## Scrittura con la Pencil e barra in alto

- **Inchiostro immediato** (menu → Opzioni): il tratto compare subito sull'iPad e sfuma quando
  arriva quello vero dal PC, così la scrittura sembra senza ritardo. Per il ritardo minimo usa
  anche la qualità **Massima** (60 fps).
- **Sotto la barra** (menu → Opzioni): l'immagine parte sotto ora, batteria e •••, che un'app
  web non può nascondere.

## Dalle altre app dell'iPad

Le app web non possono comparire tra i suggerimenti del menu Condividi (solo le app native).
Si usa un **Comando rapido** (app *Comandi* di Apple): compare in fondo al menu Condividi di
tutte le app. Passaggi nel pannello File → *Inviare dalle altre app dell'iPad*.

## Comandi sull'iPad

- **Tocco** (predefinito): le dita funzionano come su un PC touch (tocco, trascina, scorri, zoom,
  pressione prolungata = clic destro).
- **Mouse**: un dito muove e trascina, tocco con due dita = clic destro, due dita = rotellina.
- Apple Pencil: penna di Windows con pressione (vedi sopra).
- **Tre dita** o il pulsante nell'angolo in alto a destra aprono il menu
  (modalità, statistiche, ricollega, abbina di nuovo).

## Perché non si blocca

- Se il Wi-Fi ha un singhiozzo, la coda viene svuotata e si riparte dal fotogramma completo
  successivo (al massimo 1 s): il ritardo non si accumula.
- Se l'iPad non riceve nulla per 4 s si ricollega da solo; idem quando torni sull'app.
- Se FFmpeg si blocca o si chiude (cambio risoluzione, schermata UAC...) si riavvia da solo in ~1 s.
- Lo schermo dell'iPad resta acceso (Wake Lock + video muto). Per sicurezza metti comunque
  *Impostazioni iPad → Schermo e luminosità → Blocco automatico → Mai* mentre lo usi.

## Impostazioni (`config.json`)

| Chiave | Default | Note |
|---|---|---|
| `port` | 8770 | porta del server |
| `quality` | 1 | livello video: 0 Massima (60 fps), 1 Fluida (30 fps), 2 Media (75%), 3 Leggera (50%). Si cambia dal menu dell'iPad, e scende da solo se Safari si chiude per mancanza di memoria |
| `encoder` | auto | `qsv` (Intel Quick Sync, GPU) o `x264` (CPU) |

## Come funziona

- `driver/` — configurazione e installazione del *Virtual Display Driver* (schermo virtuale 4:3).
  Per reinstallarlo o cambiare le risoluzioni: `driver/Installa driver.bat`.
- `capture.py` — FFmpeg cattura lo schermo virtuale (Desktop Duplication) e lo comprime in H.264
  sulla GPU Intel, a bassa latenza.
- `server.py` — server web/WebSocket: una sessione per dispositivo (video, tocchi), accende e
  spegne gli schermi virtuali un cambio alla volta.
- `devices.py` — dispositivi abbinati e PIN.
- `vdd.py` — comandi al driver (numero di schermi, risoluzioni).
- `avvio.pyw` — avvio dal menu Start: server senza terminale + finestra (Edge in modalità app).
- `pannello.py`, `pannello/` — finestra di controllo del PC (console, prestazioni, file).
- `scambio.py` — scambio di file (cartella `download`, controllata ogni 2 s); `invia.py` è il comando di *Invia a*.
- `finestre.py` — sposta le finestre di uno schermo che si spegne sul monitor principale.
- `inject.py` — trasforma tocchi e mouse dell'iPad in input di Windows sullo schermo giusto.
- `monitors.py` — schermi di Windows: elenco, accensione/spegnimento, posizione, risoluzione, ridimensionamento.
- `web/` — l'app per l'iPad: decodifica hardware con WebCodecs e disegna su canvas.

Requisiti installati: Python 3.11 + `aiohttp`, FFmpeg (winget `Gyan.FFmpeg`),
Virtual Display Driver (winget `VirtualDrivers.Virtual-Display-Driver`).

## Problemi

- **L'iPad non si collega**: PC e iPad devono essere sulla stessa rete Wi-Fi; se Windows chiede
  il permesso del firewall per Python, consenti.
- **Scatti periodici**: meglio la rete a 5 GHz e il PC non in modalità risparmio energia.
  Spacedesk (ancora installato) non serve più e si può disinstallare.

## App nativa per iPad: file

Menu (☰) → **File condivisi**: i file della cartella `download` del PC.
- Tocca un file per l'**anteprima**; tienilo premuto per *Condividi…*, *Salva in File* oppure
  *Apri sul PC* (si apre sullo schermo dell'iPad).
- **Carica** (in alto a destra): da *Foto e video* o *Da File*, anche più elementi insieme.
- Da un'altra app (File, Safari, Mail…): *Condividi* → **Schermo Esteso** (o *Apri in…*): il file
  va subito al PC. Se l'icona non compare tra le app suggerite, scorri la fila fino a *Altro*.

## Appunti condivisi

Quello che copi sul PC (testo o immagine) arriva sui dispositivi, e viceversa.
- **App nativa:** tutto in automatico. Quello che copi sul PC è subito negli appunti dell'iPad;
  quello che copi in un'altra app dell'iPad passa al PC quando torni su Schermo Esteso. iPadOS può
  chiedere *Consentire di incollare?*: per non vederlo più, *Impostazioni → Schermo Esteso →
  Incolla da altre app → Consenti*. Menu → *Appunti* per spegnerli o inviarli a mano.
- **App web (Safari):** compare l'avviso *Appunti dal PC* con il pulsante **Copia**; nel menu,
  *Invia appunti al PC* (Safari non permette di farlo senza un tocco).
- Gli appunti che i gestori di password segnano come privati non vengono mai mandati, e la
  console non mostra mai il contenuto. Pulsante **Appunti condivisi** nella finestra del PC per
  spegnerli del tutto.

## Qualità automatica

Pulsante **Auto** accanto ai livelli di qualità nella finestra del PC (attivo di base). Se il
dispositivo non regge (ritardo oltre 250 ms, fotogrammi persi, statistiche che non arrivano), il
PC abbassa la qualità di un livello; dopo un minuto fluido la rialza, mai oltre quella scelta a
mano (bordo tratteggiato). Se rialzando non regge, la volta dopo aspetta di più.

## Scrivere sul PC e password

Menu → **Inserisci password…**: tocca prima sul PC il campo della password, poi sull'iPad scegli
la password salvata con la chiave sopra la tastiera (Face ID). Viene digitata solo in quel campo:
niente appunti, niente console. Con il nome utente compilato scrive nome, Tab e password.
**Scrivi sul PC…** fa lo stesso con un testo qualsiasi (accenti ed emoji compresi).

## Selezionare e copiare

**Doppio tocco** su una parola (con il dito, o con la Pencil dove fa da dito): sul PC la parola
si seleziona, anche nei browser, e sull'iPad compare il fumetto **Copia · Taglia · Incolla ·
Seleziona tutto**. *Copia* la mette negli appunti del PC e, con gli appunti condivisi, anche in
quelli dell'iPad. *Incolla* porta prima sul PC gli appunti dell'iPad. Nell'app web compare un
avviso con il pulsante *Copia*.

**Tieni premuto** (mezzo secondo, senza muovere il dito): compare lo stesso fumetto, con in più
*Altro (clic destro)* per il menu di Windows.

## iPhone

La stessa app nativa (`ipa\SchermoEsteso.ipa`) funziona anche su iPhone: ha il suo schermo
virtuale e il suo PIN. In orizzontale lo schermo virtuale esclude ai lati la tacca o la Dynamic
Island, così niente finisce sotto la fotocamera. Installazione come per l'iPad: iPhone col cavo,
AltServer → *Install AltStore* → iPhone, poi *Sideload .ipa*.

## Zoom e file trascinati (app nativa)

- **Zoom:** allarga o stringi due dita per ingrandire solo l'immagine sul dispositivo (fino a 5
  volte), Windows non cambia. Con l'immagine ingrandita due dita la spostano; stringendo fino in
  fondo, o dal menu *Torna al 100%*, torni alla vista intera. Al 100% due dita che si muovono
  insieme scorrono le pagine di Windows; un tocco con due dita è un clic destro.
- **Trascinare file:** trascina un file o una foto da un'altra app (File, Foto, Safari) e lascialo
  sullo schermo: arriva nella cartella condivisa e sul PC parte un trascinamento vero nello stesso
  punto, come se l'avessi trascinato con il mouse. Decide il programma che sta lì: una cartella di
  Esplora file o il desktop lo copiano, Obsidian lo mette nella cartella del vault, Word o una chat
  lo inseriscono. Se nessun programma lo prende, si apre con il suo programma (solo documenti e
  file multimediali, mai programmi o script). Serve il pacchetto `pywin32` (`pip install pywin32`).
