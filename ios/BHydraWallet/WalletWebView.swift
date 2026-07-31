import SwiftUI
import WebKit

/// Обёртка WKWebView вокруг страницы кошелька.
struct WalletWebView: UIViewRepresentable {
    let url: URL?
    let reloadToken: UUID
    let scanned: URL?

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // Хранилище ОБЯЗАТЕЛЬНО: приватный ключ кошелька живёт в localStorage.
        // Непостоянное хранилище означало бы потерю ключа при каждом закрытии.
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true

        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsBackForwardNavigationGestures = true
        if let url = url {
            view.load(URLRequest(url: url))
        }
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        if context.coordinator.token != reloadToken {
            context.coordinator.token = reloadToken
            if let url = url { view.load(URLRequest(url: url)) }
        }
        // Отсканированный адрес передаём странице тем же способом, каким его
        // разбирает веб-версия: у неё уже есть parsePaymentUri.
        if let scanned = scanned, context.coordinator.lastScan != scanned {
            context.coordinator.lastScan = scanned
            let raw = scanned.absoluteString
            let escaped = raw.replacingOccurrences(of: "\\", with: "\\\\")
                             .replacingOccurrences(of: "'", with: "\\'")
            view.evaluateJavaScript("applyScan('\(escaped)')", completionHandler: nil)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var token = UUID()
        var lastScan: URL?
    }
}
