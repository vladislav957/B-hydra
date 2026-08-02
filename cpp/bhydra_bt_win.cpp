// bhydra_bt_win.cpp — Bluetooth для B-hydra под Windows.
//
// ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ #ifdef В bhydra_bt.cpp
//
// Общего кода тут нет вовсе: у Linux это BlueZ (`hci_inquiry`, `bdaddr_t`,
// сокеты `AF_BLUETOOTH`), у Windows — Winsock (`AF_BTH`, `SOCKADDR_BTH`) и
// `bluetoothapis.h`. Совпадает только СМЫСЛ операций, а не их вид. Файл с
// #ifdef на каждой строке читался бы хуже двух honest-файлов.
//
// ПОЧЕМУ ЗДЕСЬ ВЕСЬ ТРАНСПОРТ, А НЕ ТОЛЬКО ПОИСК
//
// На Linux соединение делает сам Python: в стандартной библиотеке есть
// `AF_BLUETOOTH`/`BTPROTO_RFCOMM`, и нативным остаётся только поиск устройств.
// На Windows Bluetooth-сокетов у Python НЕТ: модуль socket не умеет разбирать
// `SOCKADDR_BTH` ни в каком виде. Поэтому здесь нативно всё — открыть, принять,
// прочитать, записать, — а Python получает объект, похожий на сокет, через
// ctypes.
//
// Собирается в ДВА артефакта из одного исходника:
//
//   bhydra_bt.exe — те же команды, что у Linux-версии (selftest/adapter/scan).
//                   Одинаковый интерфейс и JSON, поэтому поиск соседей в
//                   Python работает на обеих системах одним кодом.
//   bhydra_bt.dll — функции транспорта для ctypes (только Windows).
//
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -o bhydra_bt.exe
//       cpp/bhydra_bt_win.cpp -lws2_32 -lbthprops
//   x86_64-w64-mingw32-g++ -O2 -std=c++17 -static -shared -DBHYDRA_BT_DLL
//       -o bhydra_bt.dll cpp/bhydra_bt_win.cpp -lws2_32 -lbthprops
//
// ⚠️ Проверено только то, что можно проверить без Windows и без адаптера:
// сборка и `selftest` под Wine. Радиоканал не проверялся никем.

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <ws2bth.h>
#include <bluetoothapis.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {

// Канал RFCOMM узла B-hydra — то же число, что BLUETOOTH_CHANNEL в Python и
// kRfcommChannel в Linux-слое. Разъедься они, узлы слушали бы и звонили на
// разные каналы и молча никогда не встретились.
constexpr int kRfcommChannel = 5;

bool winsock_ready() {
    static bool started = false;
    if (!started) {
        WSADATA data;
        started = (WSAStartup(MAKEWORD(2, 2), &data) == 0);
    }
    return started;
}

// Адрес пишем как на Linux — "11:22:33:44:55:66", — чтобы Python не разбирал
// два формата. Внутри Windows это 64-битное число, старший байт первый.
std::string addr_to_mac(BTH_ADDR address) {
    char buf[18];
    std::snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X",
                  static_cast<unsigned>((address >> 40) & 0xFF),
                  static_cast<unsigned>((address >> 32) & 0xFF),
                  static_cast<unsigned>((address >> 24) & 0xFF),
                  static_cast<unsigned>((address >> 16) & 0xFF),
                  static_cast<unsigned>((address >> 8) & 0xFF),
                  static_cast<unsigned>(address & 0xFF));
    return std::string(buf);
}

// Возвращает false на любой мусор. Молча принимать испорченный адрес нельзя:
// получится соединение не туда, а выглядеть будет как «сосед не отвечает».
bool mac_to_addr(const char* text, BTH_ADDR* out) {
    if (text == nullptr) {
        return false;
    }
    unsigned values[6];
    int consumed = 0;
    if (std::sscanf(text, "%2x:%2x:%2x:%2x:%2x:%2x%n", &values[0], &values[1],
                    &values[2], &values[3], &values[4], &values[5],
                    &consumed) != 6) {
        return false;
    }
    if (text[consumed] != '\0') {
        return false;                 // хвост после адреса — тоже мусор
    }
    BTH_ADDR address = 0;
    for (int i = 0; i < 6; ++i) {
        if (values[i] > 0xFF) {
            return false;
        }
        address = (address << 8) | values[i];
    }
    *out = address;
    return true;
}

// --- Дальше только для командной строки: в DLL этих функций нет, и без
// этой границы сборка библиотеки ругалась бы на неиспользуемый код.
#ifndef BHYDRA_BT_DLL

std::string json_escape(const std::string& text) {
    std::string out;
    for (char c : text) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

std::string wide_to_utf8(const WCHAR* text) {
    if (text == nullptr || text[0] == L'\0') {
        return std::string();
    }
    int size = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr,
                                   nullptr);
    if (size <= 1) {
        return std::string();
    }
    std::string out(static_cast<size_t>(size - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, &out[0], size, nullptr, nullptr);
    return out;
}

int fail(const std::string& reason) {
    std::printf("{\"error\": \"%s\"}\n", json_escape(reason).c_str());
    return 1;
}

// --- Команды (тот же интерфейс, что у Linux-слоя) ---------------------------
int cmd_adapter() {
    BLUETOOTH_FIND_RADIO_PARAMS params;
    params.dwSize = sizeof(params);
    HANDLE radio = nullptr;
    HBLUETOOTH_RADIO_FIND finder = BluetoothFindFirstRadio(&params, &radio);
    if (finder == nullptr) {
        return fail("адаптер Bluetooth не найден");
    }
    BLUETOOTH_RADIO_INFO info;
    std::memset(&info, 0, sizeof(info));
    info.dwSize = sizeof(info);
    DWORD status = BluetoothGetRadioInfo(radio, &info);
    CloseHandle(radio);
    BluetoothFindRadioClose(finder);
    if (status != ERROR_SUCCESS) {
        return fail("не удалось прочитать сведения об адаптере");
    }
    std::printf("{\"address\": \"%s\", \"name\": \"%s\", \"channel\": %d}\n",
                json_escape(addr_to_mac(info.address.ullLong)).c_str(),
                json_escape(wide_to_utf8(info.szName)).c_str(), kRfcommChannel);
    return 0;
}

int cmd_scan(int seconds) {
    BLUETOOTH_DEVICE_SEARCH_PARAMS search;
    std::memset(&search, 0, sizeof(search));
    search.dwSize = sizeof(search);
    search.fReturnAuthenticated = TRUE;
    search.fReturnRemembered = TRUE;
    search.fReturnUnknown = TRUE;
    search.fReturnConnected = TRUE;
    search.fIssueInquiry = TRUE;
    // Множитель шагов по 1,28 с — как и у hci_inquiry в BlueZ.
    int multiplier = seconds * 100 / 128;
    if (multiplier < 1) {
        multiplier = 1;
    }
    if (multiplier > 48) {
        multiplier = 48;              // предел, заданный самим Windows
    }
    search.cTimeoutMultiplier = static_cast<UCHAR>(multiplier);
    search.hRadio = nullptr;          // все адаптеры сразу

    BLUETOOTH_DEVICE_INFO device;
    std::memset(&device, 0, sizeof(device));
    device.dwSize = sizeof(device);

    HBLUETOOTH_DEVICE_FIND finder = BluetoothFindFirstDevice(&search, &device);
    if (finder == nullptr) {
        DWORD error = GetLastError();
        if (error == ERROR_NO_MORE_ITEMS) {
            std::printf("{\"devices\": []}\n");   // рядом никого — не ошибка
            return 0;
        }
        return fail("поиск устройств не удался");
    }
    std::string out = "{\"devices\": [";
    bool first = true;
    do {
        if (!first) {
            out += ", ";
        }
        first = false;
        out += "{\"address\": \"" + json_escape(addr_to_mac(device.Address.ullLong)) +
               "\", \"name\": \"" + json_escape(wide_to_utf8(device.szName)) +
               "\", \"channel\": " + std::to_string(kRfcommChannel) + "}";
        std::memset(&device.szName, 0, sizeof(device.szName));
        device.dwSize = sizeof(device);
    } while (BluetoothFindNextDevice(finder, &device));
    BluetoothFindDeviceClose(finder);
    out += "]}";
    std::printf("%s\n", out.c_str());
    return 0;
}

// Проверяет то, что можно проверить БЕЗ Windows-машины и без адаптера: разбор
// адресов в обе стороны, отказ от мусора, экранирование JSON и то, что Winsock
// вообще поднимается. Именно это и гоняется под Wine.
int cmd_selftest() {
    BTH_ADDR address = 0;
    if (!mac_to_addr("11:22:33:44:55:66", &address)) {
        return fail("mac_to_addr не разобрал адрес");
    }
    if (address != 0x112233445566ULL) {
        return fail("mac_to_addr дал не то число");
    }
    if (addr_to_mac(address) != "11:22:33:44:55:66") {
        return fail("addr_to_mac вернул " + addr_to_mac(address));
    }
    // Мусор обязан отвергаться, а не превращаться в случайный адрес.
    BTH_ADDR ignored = 0;
    const char* junk[] = {"", "11:22:33:44:55", "11:22:33:44:55:66:77",
                          "не адрес", "11:22:33:44:55:6Z", "11:22:33:44:55:66 "};
    for (const char* bad : junk) {
        if (mac_to_addr(bad, &ignored)) {
            return fail(std::string("принят мусорный адрес: ") + bad);
        }
    }
    if (json_escape("a\"b\\c\nd") != "a\\\"b\\\\c\\nd") {
        return fail("экранирование JSON испорчено");
    }
    if (!winsock_ready()) {
        return fail("Winsock не поднялся");
    }
    std::printf("{\"ok\": true, \"channel\": %d, \"af_bth\": %d, "
                "\"sockaddr_bth_size\": %zu}\n",
                kRfcommChannel, AF_BTH, sizeof(SOCKADDR_BTH));
    return 0;
}

#endif  // BHYDRA_BT_DLL

}  // namespace

// --- Экспорт для ctypes: транспорт целиком ----------------------------------
// Возвращаются «сырые» SOCKET как long long: Python оборачивает их в объект,
// похожий на сокет (sendall/recv/settimeout/close), и выше по стеку никто не
// замечает разницы с TCP.
extern "C" {

__declspec(dllexport) int bhydra_bt_selftest(void) {
    BTH_ADDR address = 0;
    if (!mac_to_addr("AA:BB:CC:DD:EE:FF", &address)) {
        return -1;
    }
    if (addr_to_mac(address) != "AA:BB:CC:DD:EE:FF") {
        return -2;
    }
    return winsock_ready() ? 0 : -3;
}

__declspec(dllexport) long long bhydra_bt_listen(int channel) {
    if (!winsock_ready()) {
        return -1;
    }
    SOCKET server = socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM);
    if (server == INVALID_SOCKET) {
        return -1;
    }
    SOCKADDR_BTH address;
    std::memset(&address, 0, sizeof(address));
    address.addressFamily = AF_BTH;
    address.btAddr = 0;                       // на любом адаптере
    address.port = static_cast<ULONG>(channel);
    if (bind(server, reinterpret_cast<SOCKADDR*>(&address), sizeof(address)) != 0 ||
        listen(server, 8) != 0) {
        closesocket(server);
        return -1;
    }
    return static_cast<long long>(server);
}

__declspec(dllexport) long long bhydra_bt_accept(long long server, char* mac_out,
                                                 int mac_capacity) {
    SOCKADDR_BTH peer;
    int size = sizeof(peer);
    SOCKET conn = accept(static_cast<SOCKET>(server),
                         reinterpret_cast<SOCKADDR*>(&peer), &size);
    if (conn == INVALID_SOCKET) {
        return -1;
    }
    if (mac_out != nullptr && mac_capacity > 0) {
        const std::string mac = addr_to_mac(peer.btAddr);
        std::snprintf(mac_out, static_cast<size_t>(mac_capacity), "%s",
                      mac.c_str());
    }
    return static_cast<long long>(conn);
}

__declspec(dllexport) long long bhydra_bt_connect(const char* mac, int channel,
                                                  int timeout_ms) {
    if (!winsock_ready()) {
        return -1;
    }
    BTH_ADDR address = 0;
    if (!mac_to_addr(mac, &address)) {
        return -2;                            // испорченный адрес — не «нет связи»
    }
    SOCKET sock = socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM);
    if (sock == INVALID_SOCKET) {
        return -1;
    }
    if (timeout_ms > 0) {
        DWORD value = static_cast<DWORD>(timeout_ms);
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO,
                   reinterpret_cast<const char*>(&value), sizeof(value));
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO,
                   reinterpret_cast<const char*>(&value), sizeof(value));
    }
    SOCKADDR_BTH target;
    std::memset(&target, 0, sizeof(target));
    target.addressFamily = AF_BTH;
    target.btAddr = address;
    target.port = static_cast<ULONG>(channel);
    if (connect(sock, reinterpret_cast<SOCKADDR*>(&target), sizeof(target)) != 0) {
        closesocket(sock);
        return -1;
    }
    return static_cast<long long>(sock);
}

__declspec(dllexport) int bhydra_bt_send(long long sock, const char* data,
                                         int length) {
    return send(static_cast<SOCKET>(sock), data, length, 0);
}

__declspec(dllexport) int bhydra_bt_recv(long long sock, char* buffer,
                                         int capacity) {
    return recv(static_cast<SOCKET>(sock), buffer, capacity, 0);
}

__declspec(dllexport) int bhydra_bt_set_timeout(long long sock, int timeout_ms) {
    DWORD value = static_cast<DWORD>(timeout_ms < 0 ? 0 : timeout_ms);
    int first = setsockopt(static_cast<SOCKET>(sock), SOL_SOCKET, SO_RCVTIMEO,
                           reinterpret_cast<const char*>(&value), sizeof(value));
    int second = setsockopt(static_cast<SOCKET>(sock), SOL_SOCKET, SO_SNDTIMEO,
                            reinterpret_cast<const char*>(&value), sizeof(value));
    return (first == 0 && second == 0) ? 0 : -1;
}

__declspec(dllexport) int bhydra_bt_shutdown(long long sock) {
    return shutdown(static_cast<SOCKET>(sock), SD_BOTH);
}

__declspec(dllexport) int bhydra_bt_close(long long sock) {
    return closesocket(static_cast<SOCKET>(sock));
}

}  // extern "C"

#ifndef BHYDRA_BT_DLL
int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "использование: %s selftest|adapter|scan [сек]\n",
                     argv[0]);
        return 2;
    }
    const std::string command = argv[1];
    if (command == "selftest") {
        return cmd_selftest();
    }
    if (command == "adapter") {
        return cmd_adapter();
    }
    if (command == "scan") {
        return cmd_scan(argc > 2 ? std::atoi(argv[2]) : 5);
    }
    std::fprintf(stderr, "неизвестная команда: %s\n", command.c_str());
    return 2;
}
#endif
