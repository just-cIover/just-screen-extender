import AVFoundation
import UIKit

/// Il video del PC: decodificato e disegnato dall'hardware dell'iPad.
final class VideoView: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    var display: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
}

/// Inchiostro immediato: il tratto della Pencil compare subito e sfuma quando arriva quello vero
/// dal PC. In più disegna i punti "previsti" da iPadOS, che anticipano il movimento della punta.
final class InkView: UIView {
    private struct Segment { let a: CGPoint; let b: CGPoint; let width: CGFloat; let time: CFTimeInterval }
    private var segments: [Segment] = []
    private var predicted: [CGPoint] = []
    private var link: CADisplayLink?
    var lifetime: () -> Double = { 0.25 }  // secondi di visibilità (circa il ritardo del collegamento)

    override init(frame: CGRect) {
        super.init(frame: frame)
        isOpaque = false
        backgroundColor = .clear
        isUserInteractionEnabled = false
        contentMode = .redraw
    }

    required init?(coder: NSCoder) { fatalError("non usato") }

    func add(from a: CGPoint, to b: CGPoint, width: CGFloat) {
        segments.append(Segment(a: a, b: b, width: width, time: CACurrentMediaTime()))
        run()
    }

    func setPredicted(_ points: [CGPoint]) { predicted = points; run() }

    func endStroke() { predicted = []; setNeedsDisplay() }

    private func run() {
        if link == nil {
            link = CADisplayLink(target: self, selector: #selector(step))
            link?.add(to: .main, forMode: .common)
        }
        setNeedsDisplay()
    }

    @objc private func step() {
        let now = CACurrentMediaTime(), life = lifetime()
        segments.removeAll { now - $0.time > life }
        if segments.isEmpty && predicted.isEmpty {
            link?.invalidate()
            link = nil
        }
        setNeedsDisplay()
    }

    override func draw(_ rect: CGRect) {
        guard let ctx = UIGraphicsGetCurrentContext() else { return }
        ctx.setLineCap(.round)
        let now = CACurrentMediaTime(), life = lifetime()
        let ink = UIColor(red: 0.29, green: 0.31, blue: 0.35, alpha: 1)
        for s in segments {
            let alpha = max(0, min(1, 2 * (1 - (now - s.time) / life))) * 0.9
            ctx.setStrokeColor(ink.withAlphaComponent(alpha).cgColor)
            ctx.setLineWidth(s.width)
            ctx.move(to: s.a)
            ctx.addLine(to: s.b)
            ctx.strokePath()
        }
        if predicted.count > 1 {
            ctx.setStrokeColor(ink.withAlphaComponent(0.45).cgColor)
            ctx.setLineWidth(1.5)
            ctx.addLines(between: predicted)
            ctx.strokePath()
        }
    }
}

final class ScreenViewController: UIViewController, ConnectionDelegate {
    let connection = Connection()
    private let video = VideoView()
    private let ink = InkView()
    private let statusLabel = UILabel()
    private let spinner = UIActivityIndicatorView(style: .large)
    private let toast = UILabel()
    private let menuButton = UIButton(type: .system)

    private var format: CMVideoFormatDescription?
    private var videoSize = CGSize(width: 4, height: 3)
    private var waitingKey = true
    private var framesShown = 0
    private var bytesReceived = 0
    private var statsTimer: Timer?
    private var active = false
    private weak var pinAlert: UIAlertController?
    private static let qualityNames = ["Massima (60 fps)", "Fluida (30 fps)", "Media (75%)", "Leggera (50%)"]

    // tocco
    private var fingers: [ObjectIdentifier: (id: Int, x: Double, y: Double)] = [:]
    private var nextTouchId = 1
    // Pencil
    private var penTouch: UITouch?
    private var penUpAt: CFTimeInterval = 0
    private var lastPenPoint: CGPoint?
    // modalità mouse con le dita
    private var mouseFingers: [UITouch] = []
    private var dragging = false
    private var twoStart: CFTimeInterval = 0
    private var twoMoved = false
    private var lastMid: CGPoint?

    override func loadView() { view = video }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        view.isMultipleTouchEnabled = true
        video.display.videoGravity = .resizeAspect
        connection.delegate = self

        ink.frame = view.bounds
        ink.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        ink.lifetime = { [weak self] in min(0.6, max(0.15, ((self?.connection.rtt ?? 60) + 120) / 1000)) }
        view.addSubview(ink)

        statusLabel.textColor = UIColor(white: 0.7, alpha: 1)
        statusLabel.font = .systemFont(ofSize: 17)
        statusLabel.numberOfLines = 0
        statusLabel.textAlignment = .center
        spinner.color = UIColor(red: 0.95, green: 0.72, blue: 0.29, alpha: 1)
        toast.backgroundColor = UIColor(red: 0.08, green: 0.09, blue: 0.11, alpha: 0.95)
        toast.textColor = .white
        toast.font = .systemFont(ofSize: 15, weight: .semibold)
        toast.textAlignment = .center
        toast.layer.cornerRadius = 12
        toast.layer.masksToBounds = true
        toast.alpha = 0

        var cfg = UIButton.Configuration.filled()
        cfg.image = UIImage(systemName: "line.3.horizontal")
        cfg.baseBackgroundColor = UIColor(white: 0.1, alpha: 0.35)
        cfg.baseForegroundColor = UIColor(white: 1, alpha: 0.6)
        cfg.cornerStyle = .capsule
        menuButton.configuration = cfg
        menuButton.showsMenuAsPrimaryAction = true
        menuButton.menu = UIMenu(children: [UIDeferredMenuElement.uncached { [weak self] done in
            done(self?.menuItems() ?? [])
        }])
        menuButton.accessibilityLabel = "Menu"

        for v in [statusLabel, spinner, toast, menuButton] as [UIView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(v)
        }
        NSLayoutConstraint.activate([
            spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor, constant: -20),
            statusLabel.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 14),
            statusLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            statusLabel.widthAnchor.constraint(lessThanOrEqualTo: view.widthAnchor, constant: -48),
            toast.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -16),
            toast.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            toast.heightAnchor.constraint(equalToConstant: 44),
            toast.widthAnchor.constraint(lessThanOrEqualTo: view.widthAnchor, constant: -32),
            menuButton.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 6),
            menuButton.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -6),
            menuButton.widthAnchor.constraint(equalToConstant: 44),
            menuButton.heightAnchor.constraint(equalToConstant: 44),
        ])
        showStatus("Avvio…")
    }

    // schermo intero vero: niente barra di iPadOS, indicatore Home nascosto, bordi meno sensibili
    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var preferredScreenEdgesDeferringSystemGestures: UIRectEdge { .all }

    // MARK: ciclo di vita (chiamato dalla scena)

    func resume() {
        guard !active else { return }
        active = true
        if Settings.host.isEmpty {
            askHost()
        } else {
            connection.connect()
        }
        statsTimer?.invalidate()
        statsTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.sendStats() }
    }

    func pause() {
        active = false
        statsTimer?.invalidate()
        connection.close()
        UIApplication.shared.isIdleTimerDisabled = false
        video.display.flush()
        releaseInput()
    }

    // MARK: ConnectionDelegate

    func connection(_ c: Connection, status: String?) {
        if let status { showStatus(status) } else { hideStatus() }
    }

    func connectionNeedsPin(_ c: Connection, message: String?) {
        hideStatus()
        let text = (message.map { $0 + "\n\n" } ?? "") + "Questo iPad è nuovo per il PC: inserisci il PIN di 6 cifre che compare ora nella finestra di Schermo Esteso."
        if let alert = pinAlert {
            alert.message = text
            alert.textFields?.first?.text = ""
            return
        }
        let alert = UIAlertController(title: "Abbinamento", message: text, preferredStyle: .alert)
        alert.addTextField { f in
            f.keyboardType = .numberPad
            f.textContentType = .oneTimeCode
            f.placeholder = "PIN"
            f.textAlignment = .center
        }
        alert.addAction(UIAlertAction(title: "Cambia PC", style: .default) { [weak self] _ in self?.askHost() })
        alert.addAction(UIAlertAction(title: "Collega", style: .default) { [weak self, weak alert] _ in
            let pin = (alert?.textFields?.first?.text ?? "").filter(\.isNumber)
            self?.connection.sendPin(pin)
            self?.showStatus("Verifica del PIN…")
        })
        pinAlert = alert
        present(alert, animated: true)
    }

    func connectionReady(_ c: Connection) {
        pinAlert?.dismiss(animated: true)
        UIApplication.shared.isIdleTimerDisabled = true  // l'iPad non va in standby mentre fa da schermo
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        c.send(["t": "diag", "motore": "nativo", "avvio": true, "ua": "Schermo Esteso iOS \(version), \(UIDevice.current.model) iPadOS \(UIDevice.current.systemVersion)"])
        // tutto lo schermo è disponibile: lo schermo virtuale prende esattamente le sue misure
        let px = UIScreen.main.nativeBounds.size
        c.send(["t": "area", "w": Int(max(px.width, px.height)), "h": Int(min(px.width, px.height))])
    }

    func connection(_ c: Connection, config: VideoConfig) {
        format = H264.formatDescription(avcC: config.avcC)
        videoSize = CGSize(width: config.width, height: config.height)
        waitingKey = true
        video.display.flush()
        if format == nil { c.send(["t": "diag", "errore": "configurazione video non valida"]) }
    }

    func connection(_ c: Connection, frame: Data, key: Bool) {
        bytesReceived += frame.count
        guard let format else { return }
        if waitingKey && !key { return }
        let display = video.display
        if display.status == .failed {  // il decodificatore si è fermato: riparto dal prossimo fotogramma completo
            display.flush()
            waitingKey = true
            c.send(["t": "key"])
            return
        }
        guard let sample = H264.sampleBuffer(frame, format: format) else { return }
        display.enqueue(sample)
        waitingKey = false
        framesShown += 1
        if !statusLabel.isHidden { hideStatus() }
    }

    func connection(_ c: Connection, newFiles names: [String], from sender: String) {
        showToast(names.count == 1 ? "Nuovo file da \(sender): \(names[0])" : "\(names.count) nuovi file da \(sender)")
    }

    // MARK: statistiche per la finestra del PC

    private func sendStats() {
        guard connection.ready else { return }
        let q = Settings.quality
        connection.send(["t": "stats", "fps": framesShown / 2, "dec": framesShown / 2,
                         "rtt": connection.rtt.map { Int($0) as Any } ?? NSNull(),  // JSON: null se non misurato
                         "mbit": Double(bytesReceived * 8) / 2e6, "render": "nativo/hardware",
                         "q": ["Massima", "Fluida", "Media", "Leggera"][max(0, min(q, 3))]])
        framesShown = 0
        bytesReceived = 0
    }

    // MARK: coordinate

    private var contentRect: CGRect { AVMakeRect(aspectRatio: videoSize, insideRect: view.bounds) }

    private func norm(_ p: CGPoint) -> (x: Double, y: Double) {
        let r = contentRect
        guard r.width > 0, r.height > 0 else { return (0, 0) }
        return (Double((p.x - r.minX) / r.width), Double((p.y - r.minY) / r.height))
    }

    // MARK: tocchi

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches {
            if t.type == .pencil {
                penBegan(t)
            } else if penBusy {
                continue  // palmo appoggiato mentre si scrive con la Pencil
            } else if Settings.touchMode == "mouse" {
                mouseBegan(t)
            } else {
                let id = nextTouchId
                nextTouchId += 1
                let n = norm(t.location(in: view))
                fingers[ObjectIdentifier(t)] = (id, n.x, n.y)
                connection.send(["t": "p", "e": "d", "id": id, "x": n.x, "y": n.y])
            }
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches {
            if t.type == .pencil {
                penMoved(t, event)
            } else if Settings.touchMode == "mouse" {
                mouseMoved(t)
            } else if var f = fingers[ObjectIdentifier(t)] {
                let n = norm(t.location(in: view))
                f.x = n.x
                f.y = n.y
                fingers[ObjectIdentifier(t)] = f
                connection.send(["t": "p", "e": "m", "id": f.id, "x": n.x, "y": n.y])
            }
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { finish(touches, cancelled: false) }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { finish(touches, cancelled: true) }

    private func finish(_ touches: Set<UITouch>, cancelled: Bool) {
        for t in touches {
            if t.type == .pencil {
                penEnded(t, cancelled: cancelled)
            } else if Settings.touchMode == "mouse" {
                mouseEnded(t)
            } else if let f = fingers.removeValue(forKey: ObjectIdentifier(t)) {
                let n = norm(t.location(in: view))
                connection.send(["t": "p", "e": cancelled ? "c" : "u", "id": f.id, "x": n.x, "y": n.y])
            }
        }
    }

    private func releaseInput() {
        for f in fingers.values { connection.send(["t": "p", "e": "c", "id": f.id, "x": f.x, "y": f.y]) }
        fingers.removeAll()
        mouseFingers.removeAll()
        dragging = false
        penTouch = nil
        lastPenPoint = nil
        ink.endStroke()
    }

    // MARK: Apple Pencil

    private var penBusy: Bool { penTouch != nil || CACurrentMediaTime() - penUpAt < 0.6 }

    private func penBegan(_ t: UITouch) {
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "d", "b": 0, "x": n.x, "y": n.y])
            penTouch = t
            return
        }
        // il palmo era già appoggiato: annullo quei tocchi prima che la penna scriva
        for f in fingers.values { connection.send(["t": "p", "e": "c", "id": f.id, "x": f.x, "y": f.y]) }
        fingers.removeAll()
        penTouch = t
        sendPen("d", t)
        lastPenPoint = t.preciseLocation(in: view)
    }

    private func penMoved(_ t: UITouch, _ event: UIEvent?) {
        guard t === penTouch else { return }
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "m", "b": 0, "x": n.x, "y": n.y])
            return
        }
        // la Pencil misura fino a 240 punti al secondo: li mando tutti, la linea viene più fluida
        let points = event?.coalescedTouches(for: t) ?? [t]
        let drawInk = Settings.instantInk && Settings.penMode == "penna"
        for c in points {
            sendPen("m", c)
            let p = c.preciseLocation(in: view)
            if drawInk, let last = lastPenPoint { ink.add(from: last, to: p, width: 0.8 + pressure(c) * 2.4) }
            lastPenPoint = p
        }
        if drawInk, let last = lastPenPoint {
            let ahead = (event?.predictedTouches(for: t) ?? []).map { $0.preciseLocation(in: view) }
            ink.setPredicted([last] + ahead)
        }
    }

    private func penEnded(_ t: UITouch, cancelled: Bool) {
        guard t === penTouch else { return }
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
        } else {
            sendPen(cancelled ? "c" : "u", t)
        }
        penTouch = nil
        penUpAt = CACurrentMediaTime()
        lastPenPoint = nil
        ink.endStroke()
    }

    private func pressure(_ t: UITouch) -> CGFloat {
        t.maximumPossibleForce > 0 ? min(1, t.force / t.maximumPossibleForce) : 0.5
    }

    private func sendPen(_ kind: String, _ t: UITouch) {
        let n = norm(t.preciseLocation(in: view))
        // inclinazione: da altezza e direzione della Pencil agli angoli X/Y usati da Windows
        let altitude = Double(t.altitudeAngle)
        let azimuth = Double(t.azimuthAngle(in: view))
        let cot = altitude > 0.01 ? 1 / tan(altitude) : 100
        let tiltX = atan(cos(azimuth) * cot) * 180 / .pi
        let tiltY = atan(sin(azimuth) * cot) * 180 / .pi
        connection.send(["t": "pen", "e": kind, "x": n.x, "y": n.y,
                         "p": kind == "u" ? 0 : Double(pressure(t)),
                         "tx": Int(tiltX), "ty": Int(tiltY), "er": Settings.penMode == "gomma"])
    }

    // MARK: dita in modalità mouse

    private func mouseBegan(_ t: UITouch) {
        mouseFingers.append(t)
        if mouseFingers.count == 1 {
            dragging = true
            let n = norm(t.location(in: view))
            connection.send(["t": "m", "e": "d", "b": 0, "x": n.x, "y": n.y])
        } else if mouseFingers.count == 2 {
            // secondo dito: niente trascinamento, si passa a scorrimento o clic destro
            if dragging, let first = mouseFingers.first {
                let n = norm(first.location(in: view))
                connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
            }
            dragging = false
            twoStart = CACurrentMediaTime()
            twoMoved = false
            lastMid = midpoint()
        }
    }

    private func mouseMoved(_ t: UITouch) {
        if dragging, mouseFingers.count == 1 {
            let n = norm(t.location(in: view))
            connection.send(["t": "m", "e": "m", "b": 0, "x": n.x, "y": n.y])
        } else if mouseFingers.count == 2, let last = lastMid, let mid = midpoint() {
            let dx = mid.x - last.x, dy = mid.y - last.y
            if abs(dx) + abs(dy) > 2 {
                twoMoved = true
                let n = norm(mid)
                connection.send(["t": "w", "x": n.x, "y": n.y, "dx": Int(dx * 4), "dy": Int(dy * 4)])
                lastMid = mid
            }
        }
    }

    private func mouseEnded(_ t: UITouch) {
        let n = norm(t.location(in: view))
        if dragging && mouseFingers.count == 1 {
            connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
            dragging = false
        } else if mouseFingers.count == 2 && !twoMoved && CACurrentMediaTime() - twoStart < 0.35 {
            connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])  // tocco con due dita = clic destro
            twoMoved = true
        }
        mouseFingers.removeAll { $0 === t }
    }

    private func midpoint() -> CGPoint? {
        guard !mouseFingers.isEmpty else { return nil }
        let pts = mouseFingers.map { $0.location(in: view) }
        return CGPoint(x: pts.map(\.x).reduce(0, +) / CGFloat(pts.count), y: pts.map(\.y).reduce(0, +) / CGFloat(pts.count))
    }

    // MARK: menu

    private func menuItems() -> [UIMenuElement] {
        let rtt = connection.rtt.map { "\(Int($0)) ms" } ?? "–"
        let state = UIAction(title: connection.ready ? "Collegato a \(Settings.host) · ritardo \(rtt)" : "Non collegato",
                             image: UIImage(systemName: connection.ready ? "wifi" : "wifi.slash"), attributes: .disabled) { _ in }

        let quality = UIMenu(title: "Qualità", image: UIImage(systemName: "dial.medium"),
                             children: Self.qualityNames.enumerated().map { i, name in
            UIAction(title: name, state: i == Settings.quality ? .on : .off) { [weak self] _ in
                Settings.quality = i
                self?.connection.send(["t": "quality", "level": i])
            }
        })
        let fingersMenu = UIMenu(title: "Dita", image: UIImage(systemName: "hand.point.up"),
                             children: [("touch", "Tocco di Windows"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: name, state: Settings.touchMode == key ? .on : .off) { [weak self] _ in
                self?.releaseInput()
                Settings.touchMode = key
            }
        })
        let pencil = UIMenu(title: "Apple Pencil", image: UIImage(systemName: "pencil.tip"),
                            children: [("penna", "Penna con pressione"), ("gomma", "Gomma"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: name, state: Settings.penMode == key ? .on : .off) { _ in Settings.penMode = key }
        })
        let ink = UIAction(title: "Inchiostro immediato", image: UIImage(systemName: "scribble"),
                           state: Settings.instantInk ? .on : .off) { _ in Settings.instantInk.toggle() }
        let reconnect = UIAction(title: "Ricollega", image: UIImage(systemName: "arrow.clockwise")) { [weak self] _ in
            self?.connection.connect()
        }
        let repair = UIAction(title: "Abbina di nuovo", image: UIImage(systemName: "key")) { [weak self] _ in
            self?.connection.forgetPairing()
        }
        let host = UIAction(title: "Cambia PC…", image: UIImage(systemName: "desktopcomputer")) { [weak self] _ in
            self?.askHost()
        }
        return [state, quality, fingersMenu, pencil, ink,
                UIMenu(options: .displayInline, children: [reconnect, repair, host])]
    }

    private func askHost() {
        let alert = UIAlertController(title: "Collega al PC",
                                      message: "Scrivi l'indirizzo che compare in alto nella finestra di Schermo Esteso sul PC, senza https://. Esempio: 192.168.2.99:8770",
                                      preferredStyle: .alert)
        alert.addTextField { f in
            f.text = Settings.host
            f.placeholder = "192.168.2.99:8770"
            f.keyboardType = .URL
            f.autocorrectionType = .no
            f.autocapitalizationType = .none
        }
        alert.addAction(UIAlertAction(title: "Collega", style: .default) { [weak self, weak alert] _ in
            var text = alert?.textFields?.first?.text ?? ""
            for prefix in ["https://", "http://", "wss://"] where text.lowercased().hasPrefix(prefix) {
                text = String(text.dropFirst(prefix.count))
            }
            Settings.host = text.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
            self?.connection.connect()
        })
        // un alert già aperto (es. il PIN) va chiuso prima
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(alert, animated: true) }
        } else {
            present(alert, animated: true)
        }
    }

    // MARK: messaggi a schermo

    private func showStatus(_ text: String) {
        statusLabel.text = text
        statusLabel.isHidden = false
        spinner.startAnimating()
    }

    private func hideStatus() {
        statusLabel.isHidden = true
        spinner.stopAnimating()
    }

    private func showToast(_ text: String) {
        toast.text = "   \(text)   "
        UIView.animate(withDuration: 0.2) { self.toast.alpha = 1 }
        UIView.animate(withDuration: 0.4, delay: 6, options: []) { self.toast.alpha = 0 }
    }
}
