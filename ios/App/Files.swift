import PhotosUI
import QuickLook
import UIKit
import UniformTypeIdentifiers

/// Un file della cartella condivisa sul PC (D:\claudio\ipad-schermo\download).
struct SharedFile {
    let name: String
    let size: String
    let from: String
    let date: Date
}

enum FileError: LocalizedError {
    case noPC, server(Int), bad
    var errorDescription: String? {
        switch self {
        case .noPC: return "Prima collega l'app al PC."
        case .server(403): return "Il PC non riconosce più questo iPad: abbinalo di nuovo dal menu."
        case .server(404): return "Il file non c'è più sul PC."
        case .server(let code): return "Il PC ha risposto con un errore (\(code))."
        case .bad: return "Risposta del PC non valida."
        }
    }
}

/// Le stesse richieste dell'app web: elenco, download e caricamento, con la chiave del dispositivo.
enum FileAPI {
    private static func url(_ path: String, _ extra: [URLQueryItem] = []) throws -> URL {
        let host = Settings.host
        guard !host.isEmpty, !Settings.token.isEmpty else { throw FileError.noPC }
        var c = URLComponents()
        c.scheme = "https"
        let parts = host.split(separator: ":", maxSplits: 1)
        c.host = String(parts[0])
        c.port = parts.count > 1 ? Int(parts[1]) : 8770
        c.percentEncodedPath = path
        c.queryItems = [URLQueryItem(name: "dev", value: Settings.deviceId),
                        URLQueryItem(name: "token", value: Settings.token)] + extra
        guard let u = c.url else { throw FileError.noPC }
        return u
    }

    private static func check(_ response: URLResponse) throws {
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code != 200 { throw FileError.server(code) }
    }

    static func list() async throws -> [SharedFile] {
        let (data, response) = try await URLSession.shared.data(from: url("/api/file"))
        try check(response)
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let items = root["file"] as? [[String: Any]] else { throw FileError.bad }
        return items.compactMap { f in
            guard let name = f["nome"] as? String else { return nil }
            return SharedFile(name: name, size: f["dim"] as? String ?? "",
                              from: f["da"] as? String ?? "PC",
                              date: Date(timeIntervalSince1970: (f["ora"] as? Double) ?? 0))
        }
    }

    /// Scarica il file in una cartella temporanea dell'app e restituisce dove si trova.
    static func download(_ name: String) async throws -> URL {
        let segment = name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? name
        let (tmp, response) = try await URLSession.shared.download(from: url("/api/file/" + segment, [URLQueryItem(name: "scarica", value: "1")]))
        try check(response)
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("Ricevuti", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dest = dir.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tmp, to: dest)
        return dest
    }

    /// Manda i file al PC in una sola richiesta. progress va da 0 a 1.
    static func upload(_ files: [URL], progress: @escaping (Double) -> Void) async throws -> [String] {
        let boundary = "SchermoEsteso-\(UUID().uuidString)"
        let body = FileManager.default.temporaryDirectory.appendingPathComponent("invio-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: body.path, contents: nil)
        defer { try? FileManager.default.removeItem(at: body) }
        // il corpo va su disco, non in memoria: i video possono pesare parecchio
        let out = try FileHandle(forWritingTo: body)
        for file in files {
            let name = file.lastPathComponent.replacingOccurrences(of: "\"", with: "")
            out.write(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
            let input = try FileHandle(forReadingFrom: file)
            while let chunk = try input.read(upToCount: 1 << 20), !chunk.isEmpty { out.write(chunk) }
            try input.close()
            out.write(Data("\r\n".utf8))
        }
        out.write(Data("--\(boundary)--\r\n".utf8))
        try out.close()

        var request = URLRequest(url: try url("/api/invia"))
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600
        let watcher = UploadProgress(progress)
        let (data, response) = try await URLSession.shared.upload(for: request, fromFile: body, delegate: watcher)
        try check(response)
        let root = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return root?["salvati"] as? [String] ?? []
    }
}

private final class UploadProgress: NSObject, URLSessionTaskDelegate {
    let report: (Double) -> Void
    init(_ report: @escaping (Double) -> Void) { self.report = report }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        let value = Double(totalBytesSent) / Double(totalBytesExpectedToSend)
        DispatchQueue.main.async { self.report(value) }
    }
}

/// Pannello "File condivisi": quello che c'è nella cartella download del PC, uguale per tutti i dispositivi.
final class FilesViewController: UITableViewController, PHPickerViewControllerDelegate,
                                 UIDocumentPickerDelegate, QLPreviewControllerDataSource {
    var openOnPC: ((String) -> Void)?
    private var files: [SharedFile] = []
    private var previewURL: URL?
    private var busy = false
    private let dateFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .short
        f.timeStyle = .short
        return f
    }()

    init() { super.init(style: .insetGrouped) }
    required init?(coder: NSCoder) { fatalError("non usato") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "File condivisi"
        navigationItem.leftBarButtonItem = UIBarButtonItem(systemItem: .close, primaryAction: UIAction { [weak self] _ in
            self?.dismiss(animated: true)
        })
        let photos = UIAction(title: "Foto e video", image: UIImage(systemName: "photo.on.rectangle")) { [weak self] _ in
            self?.pickPhotos()
        }
        let docs = UIAction(title: "Da File", image: UIImage(systemName: "folder")) { [weak self] _ in
            self?.pickDocuments()
        }
        navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Carica", image: UIImage(systemName: "square.and.arrow.up"),
                                                            primaryAction: nil, menu: UIMenu(children: [photos, docs]))
        tableView.register(UITableViewCell.self, forCellReuseIdentifier: "file")
        refreshControl = UIRefreshControl()
        refreshControl?.addAction(UIAction { [weak self] _ in self?.reload() }, for: .valueChanged)
        reload()
    }

    // MARK: elenco

    func reload() {
        Task { @MainActor in
            do {
                files = try await FileAPI.list()
                tableView.reloadData()
                navigationItem.prompt = busy ? navigationItem.prompt : nil
            } catch {
                navigationItem.prompt = error.localizedDescription
            }
            refreshControl?.endRefreshing()
        }
    }

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int { files.count }

    override func tableView(_ tableView: UITableView, titleForFooterInSection section: Int) -> String? {
        files.isEmpty
            ? "Nessun file. Carica qualcosa da qui, trascinalo nella finestra del PC o copialo nella cartella download."
            : "Sono i file della cartella download del PC, visibili a tutti i dispositivi. Si tolgono solo cancellandoli dal PC."
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let f = files[indexPath.row]
        let cell = tableView.dequeueReusableCell(withIdentifier: "file", for: indexPath)
        var c = cell.defaultContentConfiguration()
        c.text = f.name
        c.secondaryText = "\(f.size) · da \(f.from) · \(dateFormat.string(from: f.date))"
        c.secondaryTextProperties.color = .secondaryLabel
        c.image = UIImage(systemName: Self.icon(for: f.name))
        cell.contentConfiguration = c
        cell.accessoryType = .disclosureIndicator
        return cell
    }

    private static func icon(for name: String) -> String {
        guard let type = UTType(filenameExtension: (name as NSString).pathExtension) else { return "doc" }
        if type.conforms(to: .image) { return "photo" }
        if type.conforms(to: .movie) { return "film" }
        if type.conforms(to: .audio) { return "music.note" }
        if type.conforms(to: .pdf) { return "doc.richtext" }
        if type.conforms(to: .archive) { return "doc.zipper" }
        return "doc"
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        preview(files[indexPath.row].name)
    }

    override func tableView(_ tableView: UITableView, contextMenuConfigurationForRowAt indexPath: IndexPath,
                            point: CGPoint) -> UIContextMenuConfiguration? {
        let name = files[indexPath.row].name
        return UIContextMenuConfiguration(identifier: nil, previewProvider: nil) { [weak self] _ in
            UIMenu(children: [
                UIAction(title: "Anteprima", image: UIImage(systemName: "eye")) { _ in self?.preview(name) },
                UIAction(title: "Condividi…", image: UIImage(systemName: "square.and.arrow.up")) { _ in
                    self?.share(name, at: indexPath)
                },
                UIAction(title: "Salva in File", image: UIImage(systemName: "folder.badge.plus")) { _ in self?.saveToFiles(name) },
                UIAction(title: "Apri sul PC (su questo schermo)", image: UIImage(systemName: "desktopcomputer")) { _ in
                    self?.openOnPC?(name)
                    self?.dismiss(animated: true)
                },
            ])
        }
    }

    override func tableView(_ tableView: UITableView,
                            trailingSwipeActionsConfigurationForRowAt indexPath: IndexPath) -> UISwipeActionsConfiguration? {
        let name = files[indexPath.row].name
        let share = UIContextualAction(style: .normal, title: "Condividi") { [weak self] _, _, done in
            self?.share(name, at: indexPath)
            done(true)
        }
        share.image = UIImage(systemName: "square.and.arrow.up")
        share.backgroundColor = .systemBlue
        return UISwipeActionsConfiguration(actions: [share])
    }

    // MARK: azioni su un file

    /// Scarica il file (con un messaggio di attesa) e poi esegue l'azione.
    private func fetch(_ name: String, then action: @escaping (URL) -> Void) {
        navigationItem.prompt = "Scarico \(name)…"
        Task { @MainActor in
            do {
                let url = try await FileAPI.download(name)
                navigationItem.prompt = nil
                action(url)
            } catch {
                navigationItem.prompt = error.localizedDescription
            }
        }
    }

    private func preview(_ name: String) {
        fetch(name) { [weak self] url in
            guard let self else { return }
            self.previewURL = url
            let ql = QLPreviewController()
            ql.dataSource = self
            self.present(ql, animated: true)
        }
    }

    private func share(_ name: String, at indexPath: IndexPath) {
        fetch(name) { [weak self] url in
            guard let self else { return }
            let sheet = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            if let pop = sheet.popoverPresentationController {
                pop.sourceView = self.tableView.cellForRow(at: indexPath) ?? self.tableView
                pop.sourceRect = pop.sourceView?.bounds ?? .zero
            }
            self.present(sheet, animated: true)
        }
    }

    private func saveToFiles(_ name: String) {
        fetch(name) { [weak self] url in
            let picker = UIDocumentPickerViewController(forExporting: [url], asCopy: true)
            self?.present(picker, animated: true)
        }
    }

    func numberOfPreviewItems(in controller: QLPreviewController) -> Int { previewURL == nil ? 0 : 1 }
    func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem {
        previewURL! as NSURL
    }

    // MARK: caricare

    private func pickPhotos() {
        var config = PHPickerConfiguration()
        config.selectionLimit = 0  // quanti se ne vuole
        config.preferredAssetRepresentationMode = .current  // l'originale, senza conversioni
        let picker = PHPickerViewController(configuration: config)
        picker.delegate = self
        present(picker, animated: true)
    }

    private func pickDocuments() {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.item], asCopy: true)
        picker.allowsMultipleSelection = true
        picker.delegate = self
        present(picker, animated: true)
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard controller.documentPickerMode == .import else { return }  // "Salva in File" non carica niente
        send(urls)
    }

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: true)
        guard !results.isEmpty else { return }
        navigationItem.prompt = "Preparo \(results.count) elementi…"
        Task { @MainActor in
            var urls: [URL] = []
            for r in results {
                if let u = await Self.copy(r.itemProvider) { urls.append(u) }
            }
            if urls.isEmpty {
                navigationItem.prompt = "Non sono riuscito a leggere gli elementi scelti."
            } else {
                send(urls)
            }
        }
    }

    /// Copia l'originale di una foto o di un video in una cartella temporanea (il file del sistema sparisce subito).
    private static func copy(_ provider: NSItemProvider) async -> URL? {
        let types = provider.registeredTypeIdentifiers
        let type = types.first { UTType($0)?.conforms(to: .movie) == true }
            ?? types.first { UTType($0)?.conforms(to: .image) == true }
            ?? types.first
        guard let type else { return nil }
        return await withCheckedContinuation { cont in
            provider.loadFileRepresentation(forTypeIdentifier: type) { url, _ in
                guard let url else { cont.resume(returning: nil); return }
                let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                var name = url.lastPathComponent
                if let suggested = provider.suggestedName, !suggested.isEmpty {
                    name = suggested + "." + url.pathExtension
                }
                let dest = dir.appendingPathComponent(name)
                do {
                    try FileManager.default.copyItem(at: url, to: dest)
                    cont.resume(returning: dest)
                } catch {
                    cont.resume(returning: nil)
                }
            }
        }
    }

    func send(_ urls: [URL]) {
        busy = true
        navigationItem.prompt = "Invio al PC…"
        Uploader.send(urls, progress: { [weak self] p in
            self?.navigationItem.prompt = "Invio al PC… \(Int(p * 100))%"
        }, done: { [weak self] result in
            guard let self else { return }
            self.busy = false
            switch result {
            case .success(let names):
                self.navigationItem.prompt = names.count == 1 ? "Inviato: \(names[0])" : "Inviati \(names.count) file"
                self.reload()
            case .failure(let error):
                self.navigationItem.prompt = "Invio non riuscito: \(error.localizedDescription)"
            }
        })
    }
}

/// Invio dei file al PC, usato dal pannello e dai file che arrivano da altre app (Condividi → Schermo Esteso).
enum Uploader {
    static func send(_ urls: [URL], progress: @escaping (Double) -> Void,
                     done: @escaping (Result<[String], Error>) -> Void) {
        Task { @MainActor in
            // i file arrivati da altre app sono "con accesso protetto": li apro prima di leggerli
            let opened = urls.map { $0.startAccessingSecurityScopedResource() }
            defer { for (u, ok) in zip(urls, opened) where ok { u.stopAccessingSecurityScopedResource() } }
            do {
                let names = try await FileAPI.upload(urls, progress: progress)
                done(.success(names))
            } catch {
                done(.failure(error))
            }
        }
    }
}
