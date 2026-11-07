import Foundation

/// Lingua dell'app: italiano (i testi scritti nel codice) o inglese.
/// È una sola per PC e dispositivi: il PC la manda appena ci si collega, e il menu Lingua la cambia per tutti.
/// Le frasi con parti che cambiano (nomi, numeri) hanno {} nella chiave italiana; nella traduzione {} in ordine
/// oppure {1}, {2}... per cambiarne l'ordine. Le parole che finiscono dentro le frasi ("a destra", "Massima")
/// sono in `valori`. La stessa idea della finestra del PC (pc/web/lingue/en.json).
enum Lingua {
    static let changed = Notification.Name("SchermoEstesoLinguaCambiata")
    static var current: String { Settings.language }

    static func use(_ lang: String) {
        guard lang == "it" || lang == "en", lang != Settings.language else { return }
        Settings.language = lang
        NotificationCenter.default.post(name: changed, object: nil)
    }

    private static let patterns: [(NSRegularExpression, String)] = testi.keys
        .filter { $0.contains("{}") }
        .sorted { $0.count > $1.count }   // le frasi più lunghe (più precise) per prime
        .compactMap { k in
            let rx = "^" + NSRegularExpression.escapedPattern(for: k).replacingOccurrences(of: "\\{\\}", with: "(.+?)") + "$"
            guard let re = try? NSRegularExpression(pattern: rx, options: [.dotMatchesLineSeparators]), let v = testi[k] else { return nil }
            return (re, v)
        }
    private static let placeholder = try! NSRegularExpression(pattern: "\\{(\\d*)\\}")

    static func translate(_ s: String) -> String? {
        if !s.contains("{}"), let v = testi[s] { return v }
        if let v = valori[s] { return v }
        let ns = s as NSString
        for (re, v) in patterns {
            guard let m = re.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { continue }
            let groups = (1..<m.numberOfRanges).map { part(ns.substring(with: m.range(at: $0))) }
            return fill(v, groups)
        }
        return nil
    }

    /// Una parte variabile: un valore noto, una frase intera o un elenco "a, b".
    private static func part(_ s: String) -> String {
        if let v = valori[s] { return v }
        if let r = translate(s) { return r }
        if s.contains(", ") {
            return s.components(separatedBy: ", ").map { valori[$0] ?? translate($0) ?? $0 }.joined(separator: ", ")
        }
        return s
    }

    private static func fill(_ template: String, _ parts: [String]) -> String {
        let ns = template as NSString
        var out = "", last = 0, next = 0
        for m in placeholder.matches(in: template, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let n = ns.substring(with: m.range(at: 1))
            let i = n.isEmpty ? next : (Int(n) ?? 1) - 1
            if n.isEmpty { next += 1 }
            out += i < parts.count ? parts[i] : ""
            last = m.range.location + m.range.length
        }
        return out + ns.substring(from: last)
    }

    static let valori: [String: String] = [
        "Massima": "Best", "Fluida": "Smooth", "Media": "Medium", "Leggera": "Light",
        "a destra": "to the right of", "a sinistra": "to the left of", "sopra": "above", "sotto": "below",
    ]

    static let testi: [String: String] = [
        // menu
        "Lingua": "Language",
        "Collegato a {} · ritardo {}": "Connected to {} · delay {}",
        "Non collegato": "Not connected",
        "Qualità": "Quality",
        "Massima (60 fps)": "Best (60 fps)",
        "Fluida (30 fps)": "Smooth (30 fps)",
        "Media (75%)": "Medium (75%)",
        "Leggera (50%)": "Light (50%)",
        "Dita": "Fingers",
        "Tocco di Windows": "Windows touch",
        "Mouse": "Mouse",
        "Apple Pencil": "Apple Pencil",
        "Automatica (scrive o scorre)": "Automatic (writes or scrolls)",
        "Penna con pressione": "Pen with pressure",
        "Gomma": "Eraser",
        "Ruota con il dispositivo": "Rotate with the device",
        "Inchiostro immediato": "Instant ink",
        "File condivisi": "Shared files",
        "Appunti": "Clipboard",
        "Appunti condivisi con il PC": "Clipboard shared with the PC",
        "Invia appunti al PC": "Send clipboard to the PC",
        "Inserisci password…": "Enter password…",
        "Scrivi sul PC…": "Type on the PC…",
        "Ricollega": "Reconnect",
        "Abbina di nuovo": "Pair again",
        "Cambia PC…": "Change PC…",
        "Torna al 100%": "Back to 100%",
        // collegamento e abbinamento
        "Avvio…": "Starting…",
        "Connessione al PC…": "Connecting to the PC…",
        "Riconnessione…": "Reconnecting…",
        "Connessione persa, riprovo…": "Connection lost, retrying…",
        "Nessun dato dal PC da 4 secondi.": "No data from the PC for 4 seconds.",
        "Il PC è in sospensione.": "The PC is asleep.",
        "Riprovo a collegarmi ogni 10 secondi.": "I'll try to connect every 10 seconds.",
        "Il PC non risponde: forse è in sospensione o è passato a un'altra rete.": "The PC is not responding: maybe it's asleep or moved to another network.",
        "Riprovo ogni 10 secondi.": "Retrying every 10 seconds.",
        "Indirizzo del PC non valido: {}": "Invalid PC address: {}",
        "Certificato del PC non attendibile: installalo e attivalo in Impostazioni → Generali → Info → Impostazioni attendibilità certificati.":
            "PC certificate not trusted: install it and turn it on in Settings → General → About → Certificate Trust Settings.",
        "Il dispositivo non raggiunge la rete: controlla il Wi-Fi e il permesso Rete locale (Impostazioni → Privacy e sicurezza → Rete locale).":
            "The device can't reach the network: check Wi-Fi and the Local Network permission (Settings → Privacy & Security → Local Network).",
        "Il PC non risponde: Schermo Esteso è aperto sul PC? L'indirizzo è giusto? PC e dispositivo sono sulla stessa rete?":
            "The PC is not responding: is Screen Extender open on the PC? Is the address right? Are the PC and the device on the same network?",
        "Il PC ha rifiutato il collegamento.": "The PC refused the connection.",
        "Il PC non risponde a {}: lo cerco sulle altre reti (anche l'hotspot)…": "The PC doesn't answer at {}: looking for it on the other networks (hotspot too)…",
        "PC non trovato. Schermo Esteso è aperto sul PC? PC e dispositivo sono sulla stessa rete (o il PC è collegato all'hotspot)? Riprovo…":
            "PC not found. Is Screen Extender open on the PC? Are the PC and the device on the same network (or is the PC on the hotspot)? Retrying…",
        "PC trovato: {}": "PC found: {}",
        "Il PC ha già il numero massimo di dispositivi abbinati: rimuovine uno dal PC.": "The PC already has the maximum number of paired devices: remove one from the PC.",
        "Abbinamento": "Pairing",
        "Questo {} è nuovo per il PC: inserisci il PIN di 6 cifre che compare ora nella finestra di Schermo Esteso.":
            "This {} is new to the PC: enter the 6-digit PIN that now appears in the Screen Extender window.",
        "Troppi tentativi sbagliati: riprova tra {} minuti.": "Too many wrong attempts: try again in {} minutes.",
        "Troppe richieste di abbinamento in corso: riprova tra poco.": "Too many pairing requests in progress: try again shortly.",
        "Il PIN è cambiato: inserisci quello nuovo che vedi sul PC.": "The PIN has changed: enter the new one shown on the PC.",
        "PIN errato, riprova.": "Wrong PIN, try again.",
        "Verifica del PIN…": "Checking the PIN…",
        "Cambia PC": "Change PC",
        "Collega": "Connect",
        "Collega al PC": "Connect to the PC",
        "Scrivi l'indirizzo che compare in alto nella finestra di Schermo Esteso sul PC, senza https://. Esempio: 192.168.1.20:8770":
            "Type the address shown at the top of the Screen Extender window on the PC, without https://. Example: 192.168.1.20:8770",
        "Qualità {}: impostata in automatico dal PC": "Quality {}: set automatically by the PC",
        // spinta del mouse
        "Schermo {} del PC?": "Screen {} the PC?",
        "Hai spinto il mouse {}. Se questo {} si trova lì, tocca Qui e il suo schermo si sposta da quella parte.":
            "You pushed the mouse {1} the PC. If this {2} is there, tap Here and its screen moves to that side.",
        "Qui": "Here",
        // scrivere sul PC
        "Inserisci password": "Enter password",
        "Tocca prima sul PC il campo della password (o del nome utente). Poi scegli qui la password salvata: la tastiera mostra la chiave delle Password.":
            "First tap the password (or user name) field on the PC. Then pick the saved password here: the keyboard shows the Passwords key.",
        "Nome utente (facoltativo)": "User name (optional)",
        "Password": "Password",
        "Annulla": "Cancel",
        "Inserisci": "Insert",
        "Inserisci e premi Invio": "Insert and press Enter",
        "Scrivi sul PC": "Type on the PC",
        "Il testo viene scritto nel punto selezionato sul PC, come con una tastiera.": "The text is typed where the cursor is on the PC, as with a keyboard.",
        "Testo": "Text",
        "Scrivi": "Type",
        "Scrivi e premi Invio": "Type and press Enter",
        "Copia": "Copy",
        "Taglia": "Cut",
        "Incolla": "Paste",
        "Seleziona tutto": "Select all",
        "Altro (clic destro)": "More (right click)",
        // appunti
        "Appunti dal PC copiati su {}": "Clipboard from the PC copied to {}",
        "Immagine dagli appunti del PC copiata su {}": "Image from the PC clipboard copied to {}",
        "Appunti di {} copiati sul PC": "{} clipboard copied to the PC",
        "Immagine di {} copiata negli appunti del PC": "{} image copied to the PC clipboard",
        "Appunti non inviati: {}": "Clipboard not sent: {}",
        "Gli appunti di {} sono vuoti": "The {} clipboard is empty",
        // file
        "Nuovo file da {}: {}": "New file from {}: {}",
        "{} nuovi file da {}": "{} new files from {}",
        "Invio al PC…": "Sending to the PC…",
        "Invio al PC di {} elementi…": "Sending {} items to the PC…",
        "Invio al PC: {}": "Sending to the PC: {}",
        "Invio al PC di {} file": "Sending {} files to the PC",
        "Inviato al PC": "Sent to the PC",
        "Inviato al PC: {}": "Sent to the PC: {}",
        "Inviati al PC {} file": "Sent {} files to the PC",
        "Inviati {} file": "Sent {} files",
        "Inviato: apro {} sul PC": "Sent: opening {} on the PC",
        "Inviati {} file: apro {}": "Sent {} files: opening {}",
        "Inviato: {}": "Sent: {}",
        "Invio non riuscito: {}": "Sending failed: {}",
        "Non riesco a leggere quello che hai trascinato": "I can't read what you dragged",
        "Apro {} sul PC, su questo schermo…": "Opening {} on the PC, on this screen…",
        "Prima collega l'app al PC: invierò il file subito dopo": "Connect the app to the PC first: I'll send the file right after",
        "Prima collega l'app al PC.": "Connect the app to the PC first.",
        "Il PC non riconosce più questo dispositivo: abbinalo di nuovo dal menu.": "The PC no longer recognizes this device: pair it again from the menu.",
        "Il file non c'è più sul PC.": "The file is no longer on the PC.",
        "Il PC ha risposto con un errore ({}).": "The PC answered with an error ({}).",
        "Risposta del PC non valida.": "Invalid answer from the PC.",
        "Foto e video": "Photos and videos",
        "Da File": "From Files",
        "Carica": "Upload",
        "Nessun file. Carica qualcosa da qui, trascinalo nella finestra del PC o copialo nella cartella download.":
            "No files. Upload something from here, drag it into the PC window or copy it into the downloads folder.",
        "Sono i file della cartella download del PC, visibili a tutti i dispositivi. Si tolgono solo cancellandoli dal PC.":
            "These are the files in the PC downloads folder, visible to every device. They go away only when you delete them on the PC.",
        "{} · da {} · {}": "{} · from {} · {}",
        "Anteprima": "Preview",
        "Condividi…": "Share…",
        "Condividi": "Share",
        "Salva in File": "Save to Files",
        "Apri sul PC (su questo schermo)": "Open on the PC (on this screen)",
        "Scarico {}…": "Downloading {}…",
        "Preparo {} elementi…": "Preparing {} items…",
        "Non sono riuscito a leggere gli elementi scelti.": "I couldn't read the chosen items.",
        "{} di {}": "{} of {}",
        "Inviato, il PC lo sta salvando…": "Sent, the PC is saving it…",
        "Completato": "Done",
        "Riprova: il file non è arrivato al PC": "Try again: the file didn't reach the PC",
        "{}%  ·  fermo da {} s: la rete è lenta o il PC non risponde": "{}%  ·  stuck for {} s: the network is slow or the PC is not responding",
    ]
}

/// Il testo nella lingua dell'app. Si traduce riga per riga (i messaggi di stato ne hanno più d'una);
/// in italiano, o senza traduzione, resta com'è.
func L(_ s: String) -> String {
    guard Lingua.current == "en" else { return s }
    return s.components(separatedBy: "\n").map { line in
        let core = line.trimmingCharacters(in: .whitespaces)
        guard !core.isEmpty, let r = Lingua.translate(core) else { return line }
        return line.replacingOccurrences(of: core, with: r)
    }.joined(separator: "\n")
}
