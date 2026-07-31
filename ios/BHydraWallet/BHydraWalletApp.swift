import SwiftUI

/// Точка входа приложения.
///
/// Криптографии в нативном коде нет вовсе: ключ, подпись транзакции, QR и
/// выбор узла делает сама страница кошелька (bhydra-sign.js, bhydra-qr.js,
/// bhydra-net.js) внутри WKWebView. Swift здесь — окно, камера и настройка
/// адреса узла.
///
/// Вторая реализация подписи под iOS означала бы ЧЕТВЁРТЫЙ независимый код для
/// одного формата (после Python, JS и C++) и четвёртый источник расхождений.
@main
struct BHydraWalletApp: App {
    @StateObject private var settings = Settings()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(settings)
                // QR, снятый системной «Камерой», открывает кошелёк по ссылке
                // bhydra:<адрес> — этого веб-версия на iOS сделать не может.
                .onOpenURL { url in settings.pendingPayment = url }
        }
    }
}

/// Настройки приложения. Адрес узла задаёт пользователь: приложение не
/// привязано к одному серверу.
final class Settings: ObservableObject {
    private static let nodeKey = "bhydra.node"
    /// localhost хозяйской машины из симулятора; на устройстве нужен адрес
    /// узла в локальной сети.
    static let defaultNode = "http://127.0.0.1:8000"

    @Published var node: String {
        didSet { UserDefaults.standard.set(node, forKey: Settings.nodeKey) }
    }
    @Published var pendingPayment: URL?

    init() {
        node = UserDefaults.standard.string(forKey: Settings.nodeKey)
            ?? Settings.defaultNode
    }

    var walletURL: URL? { URL(string: node + "/wallet") }
}
