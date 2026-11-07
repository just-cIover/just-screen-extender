import Foundation
import Network
import QuartzCore
import UIKit

struct VideoConfig {
    let width: Int
    let height: Int
    let fps: Int
    let avcC: Data
}

protocol ConnectionDelegate: AnyObject {
    func connection(_ c: Connection, status: String?)            // nil = all good
    func connectionNeedsPin(_ c: Connection, message: String?)
    func connectionReady(_ c: Connection)
    func connection(_ c: Connection, config: VideoConfig)
    func connection(_ c: Connection, frame: Data, key: Bool)
    func connection(_ c: Connection, newFiles names: [String], from sender: String)
}

/// Encrypted connection (WebSocket over HTTPS) to the server on the PC: same protocol as the web app.
final class Connection: NSObject, URLSessionWebSocketDelegate {
    weak var delegate: ConnectionDelegate?
    var onPenUse: ((String) -> Void)?             // "penna" or "dito": how the PC uses the Pencil in the current stroke
    var onClipboard: (([String: Any]) -> Void)?  // clipboard copied on the PC
    var onNotice: ((String) -> Void)?             // short notices (e.g. quality changed automatically)
    var onWhere: ((String, Int) -> Void)?   // the PC asks whether the screen goes on this side (mouse push)
    var onWhereDone: (() -> Void)?           // another device answered
    var onRest: ((Bool) -> Void)?            // true: the PC is asleep (or gone for a minute), the screen may turn off
    var onWordSelected: (() -> Void)?            // the PC selected the word (double tap)
    private(set) var ready = false
    private(set) var rtt: Double?          // round-trip delay in ms
    private(set) var pairing = false
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var generation = 0
    private var retry = 0
    private var lastMessage = Date()
    private var timer: Timer?
    private var holdUntil = Date.distantPast  // pairing blocked by the PC: no burst of attempts
    private var offlineSince: Date?           // since when the PC hasn't been answering
    private var resting = false               // the PC is asleep: the device doesn't keep its screen on
    private var pcAsleep = false              // the PC said so ("riposo"): no need to look for it on other networks

    /// Pause before retrying: none at first; after a minute without the PC (or with the PC asleep)
    /// every 10 seconds, to save battery
    private var retryDelay: Double {
        calm ? 10 : min(0.3 * Double(retry), 2)
    }

    func resetOffline() {
        offlineSince = nil
        resting = false  // it will hear it again from the PC, if it's still asleep
        pcAsleep = false
        retry = 0
    }

    /// How many seconds the PC hasn't been answering (0 if connected).
    private var offlineFor: Double { offlineSince.map { Date().timeIntervalSince($0) } ?? 0 }

    /// After 10 s without the PC (or if the PC said it's asleep) attempts go on quietly with a single fixed message:
    /// a PC going to sleep cuts the network before it can say so, and messages that keep changing are confusing.
    private var calm: Bool { pcAsleep || offlineFor > 10 }

    /// Status message; in the quiet state it always stays the same.
    private func say(_ text: String) {
        delegate?.connection(self, status: calm
            ? (pcAsleep ? "Il PC è in sospensione.\nRiprovo a collegarmi ogni 10 secondi."
                        : "Il PC non risponde: forse è in sospensione o è passato a un'altra rete.\nRiprovo ogni 10 secondi.")
            : text)
    }

    private func setResting(_ r: Bool) {
        guard r != resting else { return }
        resting = r
        onRest?(r)
    }

    override init() {
        super.init()
        let cfg = URLSessionConfiguration.default
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: .main)
    }

    // MARK: connection

    func connect() {
        close()
        let host = Settings.host
        guard !host.isEmpty else { return }
        var comps = URLComponents()
        comps.scheme = "wss"
        let parts = host.split(separator: ":", maxSplits: 1)
        comps.host = String(parts[0])
        comps.port = parts.count > 1 ? Int(parts[1]) : 8770
        comps.path = "/ws"
        comps.queryItems = [URLQueryItem(name: "dev", value: Settings.deviceId),
                            URLQueryItem(name: "token", value: Settings.token)]
        guard let url = comps.url else {
            delegate?.connection(self, status: "Indirizzo del PC non valido: \(host)")
            return
        }
        generation += 1
        let gen = generation
        let t = session.webSocketTask(with: url)
        t.maximumMessageSize = 16 << 20
        task = t
        t.resume()
        receive(gen)
        lastMessage = Date()
        say(retry > 0 ? "Riconnessione…" : "Connessione al PC…")
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
    }

    func close() {
        generation += 1
        timer?.invalidate()
        timer = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        ready = false
    }

    private func tick() {
        if ready { send(["t": "ping", "ts": CACurrentMediaTime() * 1000]) }
        // no data for 4 s (Wi-Fi asleep, PC paused…): restart
        if !pairing && Date().timeIntervalSince(lastMessage) > 4 { lost() }
    }

    /// Readable explanation of the error, with the technical code to understand the cause.
    private func describe(_ error: Error) -> String {
        let ns = error as NSError
        let hint: String
        switch ns.code {
        case NSURLErrorServerCertificateUntrusted, NSURLErrorServerCertificateHasUnknownRoot,
             NSURLErrorServerCertificateNotYetValid, NSURLErrorServerCertificateHasBadDate, NSURLErrorSecureConnectionFailed:
            hint = "Certificato del PC non attendibile: installalo e attivalo in Impostazioni → Generali → Info → Impostazioni attendibilità certificati."
        case NSURLErrorNotConnectedToInternet, NSURLErrorNetworkConnectionLost:
            hint = "Il dispositivo non raggiunge la rete: controlla il Wi-Fi e il permesso Rete locale (Impostazioni → Privacy e sicurezza → Rete locale)."
        case NSURLErrorCannotConnectToHost, NSURLErrorTimedOut, NSURLErrorCannotFindHost:
            hint = "Il PC non risponde: Screen Extender è aperto sul PC? L'indirizzo è giusto? PC e dispositivo sono sulla stessa rete?"
        case NSURLErrorBadServerResponse:
            hint = "Il PC ha rifiutato il collegamento."
        default:
            hint = ns.localizedDescription
        }
        return "\(hint)\n(\(ns.domain) \(ns.code))"
    }

    private func lost(_ error: Error? = nil) {
        let wasPairing = pairing
        close()
        pairing = false
        if wasPairing && Date() < holdUntil {
            DispatchQueue.main.asyncAfter(deadline: .now() + min(holdUntil.timeIntervalSinceNow, 60)) { [weak self] in
                self?.connect()
            }
            return
        }
        retry += 1
        if offlineSince == nil { offlineSince = Date() }
        if offlineFor > 30 { setResting(true) }  // half a minute without the PC: the device screen may turn off
        if pcAsleep {
            // the PC is asleep: no search on other networks (it wouldn't answer), one attempt every 10 s
            say("")
            DispatchQueue.main.asyncAfter(deadline: .now() + retryDelay) { [weak self] in
                guard let self, self.task == nil else { return }
                self.connect()
            }
            return
        }
        // no answer (or network error) twice: maybe the PC changed network (hotspot)
        if retry >= 2 && (error == nil || Self.unreachable(error!)) {
            findPC()  // the PC doesn't answer at that address: maybe it changed network (hotspot)
            return
        }
        let reason = error.map { describe($0) } ?? "Nessun dato dal PC da 4 secondi."
        say("Connessione persa, riprovo…\n\n\(reason)")
        DispatchQueue.main.asyncAfter(deadline: .now() + retryDelay) { [weak self] in
            guard let self, self.task == nil else { return }
            self.connect()
        }
    }

    private func receive(_ gen: Int) {
        task?.receive { [weak self] result in
            guard let self, gen == self.generation else { return }
            switch result {
            case .failure(let error):
                self.lost(error)
            case .success(let message):
                self.lastMessage = Date()
                switch message {
                case .data(let d):
                    if let first = d.first, d.count > 1 {
                        self.delegate?.connection(self, frame: d.subdata(in: (d.startIndex + 1)..<d.endIndex), key: first == 1)
                    }
                case .string(let s):
                    self.handle(s)
                @unknown default:
                    break
                }
                self.receive(gen)
            }
        }
    }

    // MARK: looking for the PC on other networks

    private var finding = false

    private static func unreachable(_ error: Error) -> Bool {
        let code = (error as NSError).code
        return [NSURLErrorCannotConnectToHost, NSURLErrorTimedOut, NSURLErrorCannotFindHost,
                NSURLErrorNetworkConnectionLost, NSURLErrorNotConnectedToInternet].contains(code)
            || (error as NSError).domain == NSPOSIXErrorDomain
    }

    /// Tries the known PC addresses and the iPhone hotspot ones (172.20.10.2-14) together:
    /// the first that answers on the Screen Extender port becomes the address to use.
    private func findPC() {
        guard !finding else { return }
        finding = true
        let current = Settings.host
        let port = current.split(separator: ":").dropFirst().first.map(String.init) ?? "8770"
        var candidates = [current] + Settings.knownHosts
        candidates += (2...14).map { "172.20.10.\($0):\(port)" }
        var seen = Set<String>()
        candidates = candidates.filter { !$0.isEmpty && seen.insert($0).inserted }
        say("Il PC non risponde a \(current): lo cerco sulle altre reti (anche l'hotspot)…")
        var done = false
        var probes: [NWConnection] = []
        for c in candidates {
            let parts = c.split(separator: ":", maxSplits: 1)
            guard let host = parts.first, let p = UInt16(parts.count > 1 ? String(parts[1]) : port),
                  let nwPort = NWEndpoint.Port(rawValue: p) else { continue }
            let probe = NWConnection(host: NWEndpoint.Host(String(host)), port: nwPort, using: .tcp)
            probe.stateUpdateHandler = { [weak self] state in
                guard case .ready = state else { return }
                probe.cancel()
                guard !done, let self else { return }
                done = true
                self.found(c)
            }
            probes.append(probe)
            probe.start(queue: .main)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
            probes.forEach { $0.cancel() }
            guard let self else { return }
            self.finding = false
            if !done {
                self.say("PC non trovato. Screen Extender è aperto sul PC? PC e dispositivo sono sulla stessa rete (o il PC è collegato all'hotspot)? Riprovo…")
                DispatchQueue.main.asyncAfter(deadline: .now() + max(2, self.retryDelay)) { [weak self] in self?.connect() }
            }
        }
    }

    private func found(_ host: String) {
        finding = false
        if host != Settings.host {
            Settings.host = host
            onNotice?("PC trovato: \(host)")
        }
        retry = 0
        connect()
    }

    // MARK: messages from the PC

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let m = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let t = m["t"] as? String else { return }
        switch t {
        case "pair":
            pairing = true
            send(["t": "hello", "nome": UIDevice.current.model])  // "iPad": the same name as the web app
            delegate?.connectionNeedsPin(self, message: nil)
        case "paired":
            if let token = m["token"] as? String { Settings.token = token }
        case "pinerr":
            if let secs = m["bloccato"] as? Int {
                holdUntil = Date().addingTimeInterval(Double(secs))
                delegate?.connectionNeedsPin(self, message: "Troppi tentativi sbagliati: riprova tra \(Int(ceil(Double(secs) / 60))) minuti.")
            } else if m["occupato"] as? Bool == true {
                holdUntil = Date().addingTimeInterval(30)
                delegate?.connectionNeedsPin(self, message: "Troppe richieste di abbinamento in corso: riprova tra poco.")
            } else if m["nuovo"] as? Bool == true {
                delegate?.connectionNeedsPin(self, message: "Il PIN è cambiato: inserisci quello nuovo che vedi sul PC.")
            } else {
                delegate?.connectionNeedsPin(self, message: "PIN errato, riprova.")
            }
        case "hello":
            if let addrs = m["indirizzi"] as? [String] {
                var known = Settings.knownHosts
                for a in addrs + [Settings.host] where !known.contains(a) { known.insert(a, at: 0) }
                Settings.knownHosts = known
            }
            pairing = false
            ready = true
            retry = 0
            offlineSince = nil
            pcAsleep = false
            setResting(false)
            delegate?.connection(self, status: nil)
            delegate?.connectionReady(self)
        case "config":
            guard let w = m["w"] as? Int, let h = m["h"] as? Int,
                  let b64 = m["desc"] as? String, let avcC = Data(base64Encoded: b64) else { return }
            delegate?.connection(self, config: VideoConfig(width: w, height: h, fps: m["fps"] as? Int ?? 30, avcC: avcC))
        case "quality":
            // the PC remembers the quality (and can enforce it): I keep the menu in sync
            if let level = m["level"] as? Int {
                Settings.quality = level
                if m["auto"] as? Bool == true {
                    let names = ["Massima", "Fluida", "Media", "Leggera"]
                    onNotice?("Qualità \(names[max(0, min(level, 3))]): impostata in automatico dal PC")
                }
            }
        case "parola_ok":
            onWordSelected?()
        case "appunti":
            onClipboard?(m)
        case "penna":
            if let use = m["uso"] as? String { onPenUse?(use) }
        case "pong":
            if let ts = m["ts"] as? Double, ts >= pingsAfter { rtt = CACurrentMediaTime() * 1000 - ts }
        case "file":
            let names = m["nomi"] as? [String] ?? []
            if !names.isEmpty { delegate?.connection(self, newFiles: names, from: m["da"] as? String ?? "PC") }
        case "lingua":  // the PC language applies to everyone: it arrives right after connecting and whenever someone changes it
            if let lang = m["lingua"] as? String { Lingua.use(lang) }
        case "avviso":
            if let text = m["testo"] as? String { onNotice?(text) }
        case "dove":
            onWhere?(m["lato"] as? String ?? "", m["secondi"] as? Int ?? 15)
        case "dove_fine":
            onWhereDone?()
        case "riposo":
            pcAsleep = true
            setResting(true)
            say("")  // fixed sleep message
        case "sveglio":
            pcAsleep = false
            setResting(false)
            delegate?.connection(self, status: nil)
        case "full":
            delegate?.connection(self, status: "Il PC ha già il numero massimo di dispositivi abbinati: rimuovine uno dal PC.")
        default:
            break
        }
    }

    // MARK: messages to the PC

    /// After the app was paused: answers to pings sent before would give huge, false delays.
    func forgetDelay() {
        rtt = nil
        pingsAfter = CACurrentMediaTime() * 1000
    }
    private var pingsAfter: Double = 0

    func send(_ obj: [String: Any]) {
        guard let task, let data = try? JSONSerialization.data(withJSONObject: obj),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { _ in }
    }

    func sendPin(_ pin: String) {
        let screen = UIScreen.main.nativeBounds.size  // real pixels
        send(["t": "pin", "pin": pin, "nome": UIDevice.current.model,
              "w": Int(max(screen.width, screen.height)), "h": Int(min(screen.width, screen.height)),
              "dpr": Double(UIScreen.main.nativeScale)])
    }

    /// Forget the key: on the next connection the PC will ask for a new PIN.
    func forgetPairing() {
        Settings.token = ""
        connect()
    }
}
