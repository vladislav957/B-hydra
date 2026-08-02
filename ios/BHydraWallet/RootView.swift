import SwiftUI
import WebKit

struct RootView: View {
    @EnvironmentObject private var settings: Settings
    @State private var showingNodeSheet = false
    @State private var showingScanner = false
    @State private var reloadToken = UUID()

    var body: some View {
        NavigationView {
            WalletWebView(url: settings.walletURL, reloadToken: reloadToken,
                          scanned: settings.pendingPayment)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("B-hydra")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItemGroup(placement: .navigationBarTrailing) {
                        Button {
                            showingScanner = true
                        } label: { Image(systemName: "qrcode.viewfinder") }
                        Button {
                            showingNodeSheet = true
                        } label: { Image(systemName: "server.rack") }
                        Button {
                            reloadToken = UUID()
                        } label: { Image(systemName: "arrow.clockwise") }
                    }
                }
        }
        .navigationViewStyle(.stack)
        .sheet(isPresented: $showingNodeSheet) { NodeSheet() }
        .sheet(isPresented: $showingScanner) {
            // Сканер камерой — то, чего веб-версии на iOS не хватает:
            // BarcodeDetector в Safari не реализован.
            ScannerView { value in
                showingScanner = false
                settings.pendingPayment = URL(string: value)
                    ?? URL(string: "bhydra:" + value)
            }
        }
    }
}

/// Ввод адреса узла.
struct NodeSheet: View {
    @EnvironmentObject private var settings: Settings
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""

    var body: some View {
        NavigationView {
            Form {
                Section("Адрес узла") {
                    TextField("192.168.0.10:8000", text: $draft)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    Text("Кошелёк спрашивает все известные узлы и берёт "
                         + "цепочку с наибольшей работой. Этот адрес — тот, "
                         + "с которого загружается само приложение.")
                        .font(.footnote)
                }
            }
            .navigationTitle("Узел")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Отмена") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Готово") {
                        var value = draft.trimmingCharacters(in: .whitespaces)
                        if !value.isEmpty {
                            if !value.hasPrefix("http://") && !value.hasPrefix("https://") {
                                value = "http://" + value
                            }
                            settings.node = value
                        }
                        dismiss()
                    }
                }
            }
            .onAppear { draft = settings.node }
        }
    }
}
