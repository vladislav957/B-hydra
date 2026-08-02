// bhydra_bt.cpp — нативный слой Bluetooth для B-hydra (D2D без роутера).
//
// ЗАЧЕМ ЗДЕСЬ C++, если сокеты есть и в Python.
//
// Само соединение по RFCOMM Python умеет сам: в стандартной библиотеке есть
// AF_BLUETOOTH и BTPROTO_RFCOMM, и `BluetoothTransport` в b_hydra/transport.py
// написан на них — без единой зависимости. Здесь другое: ПОИСК УСТРОЙСТВ
// ПОБЛИЗОСТИ. Его в стандартной библиотеке нет вообще — нужен hci_inquiry из
// BlueZ, а это C API. Именно поиск и делает связь D2D: узлы находят друг друга
// там, где нет ни роутера, ни интернета, ни общего адресного пространства.
//
// То есть слой не дублирует Python, а закрывает то, чего в нём нет. Скорость
// тут ни при чём (в отличие от bhydra_secure.hpp, где C++ ускоряет кривую в
// 29 раз): системный вызов быстрее от языка не станет.
//
// Сборка:
//     g++ -O2 -std=c++17 -o bhydra_bt cpp/bhydra_bt.cpp -lbluetooth
//
// Команды (вывод — JSON, чтобы его читал Python):
//     selftest             проверка сборки и разбора адресов, без адаптера
//     adapter              свой адрес и имя
//     scan [сек]           устройства поблизости
//
// ⚠️ Всё, кроме selftest, требует адаптера Bluetooth и прав на него. Без
// адаптера команды отвечают {"error": ...}, а не падают: узел обязан пережить
// отсутствие железа.

#include <bluetooth/bluetooth.h>
#include <bluetooth/hci.h>
#include <bluetooth/hci_lib.h>

#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

// Канал RFCOMM, на котором живёт узел B-hydra. Это ровно то же, что порт 5000
// у TCP: обе стороны должны знать его заранее. Взять его из SDP было бы
// правильнее, но SDP — ещё сотня строк, которую здесь НЕЧЕМ ПРОВЕРИТЬ (нет
// адаптера), а непроверенный код в этом проекте не заводят.
constexpr int kRfcommChannel = 5;

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

int fail(const std::string& reason) {
    std::printf("{\"error\": \"%s\"}\n", json_escape(reason).c_str());
    return 1;
}

// Открывает первый доступный адаптер. dev_id возвращается отдельно: он нужен
// и для inquiry, и для чтения собственного адреса.
int open_adapter(int* dev_id) {
    *dev_id = hci_get_route(nullptr);
    if (*dev_id < 0) {
        return -1;
    }
    return hci_open_dev(*dev_id);
}

int cmd_adapter() {
    int dev_id = -1;
    int sock = open_adapter(&dev_id);
    if (sock < 0) {
        return fail("адаптер Bluetooth не найден");
    }
    bdaddr_t self{};
    char address[19] = {0};
    char name[249] = {0};
    if (hci_devba(dev_id, &self) < 0) {
        ::close(sock);
        return fail("не удалось прочитать адрес адаптера");
    }
    ba2str(&self, address);
    if (hci_read_local_name(sock, sizeof(name), name, 1000) < 0) {
        std::strcpy(name, "");
    }
    ::close(sock);
    std::printf("{\"address\": \"%s\", \"name\": \"%s\", \"channel\": %d}\n",
                json_escape(address).c_str(), json_escape(name).c_str(),
                kRfcommChannel);
    return 0;
}

// Поиск соседей. Возвращает ВСЕ видимые устройства: наушники и телефоны в том
// числе. Отличить свои узлы отсюда нельзя — это делает уже рукопожатие
// B-hydra, где сверяется отпечаток сети. Так же устроен и UDP-маяк: он тоже
// летит всем, а чужие отсеиваются по genesis.
int cmd_scan(int seconds) {
    int dev_id = -1;
    int sock = open_adapter(&dev_id);
    if (sock < 0) {
        return fail("адаптер Bluetooth не найден");
    }
    // Длительность inquiry задаётся шагами по 1,28 с — так в спецификации.
    int length = seconds * 100 / 128;
    if (length < 1) {
        length = 1;
    }
    constexpr int kMaxDevices = 255;
    inquiry_info* found = static_cast<inquiry_info*>(
        std::calloc(kMaxDevices, sizeof(inquiry_info)));
    if (found == nullptr) {
        ::close(sock);
        return fail("не хватило памяти под список устройств");
    }
    int count = hci_inquiry(dev_id, length, kMaxDevices, nullptr, &found,
                            IREQ_CACHE_FLUSH);
    if (count < 0) {
        std::free(found);
        ::close(sock);
        return fail("поиск устройств не удался");
    }
    std::string out = "{\"devices\": [";
    for (int i = 0; i < count; ++i) {
        char address[19] = {0};
        char name[249] = {0};
        ba2str(&(found + i)->bdaddr, address);
        // Имя читается отдельным запросом и может не ответить — устройство
        // вправе молчать. Это не ошибка поиска, поэтому просто пустое имя.
        if (hci_read_remote_name(sock, &(found + i)->bdaddr, sizeof(name), name,
                                 2000) < 0) {
            std::strcpy(name, "");
        }
        if (i > 0) {
            out += ", ";
        }
        out += "{\"address\": \"" + json_escape(address) + "\", \"name\": \"" +
               json_escape(name) + "\", \"channel\": " +
               std::to_string(kRfcommChannel) + "}";
    }
    out += "]}";
    std::free(found);
    ::close(sock);
    std::printf("%s\n", out.c_str());
    return 0;
}

// Проверка того, что можно проверить БЕЗ железа: сборка, разбор адресов и
// экранирование JSON. Без этого «собралось» оставалось бы единственным
// доказательством, а оно ничего не значит.
int cmd_selftest() {
    bdaddr_t address{};
    if (str2ba("11:22:33:44:55:66", &address) < 0) {
        return fail("str2ba не разобрал адрес");
    }
    char back[19] = {0};
    ba2str(&address, back);
    if (std::string(back) != "11:22:33:44:55:66") {
        return fail("ba2str вернул " + std::string(back));
    }
    // Адрес хранится в обратном порядке байтов — проверяем, что мы работаем
    // с настоящей структурой BlueZ, а не с чем-то похожим.
    if (address.b[0] != 0x66 || address.b[5] != 0x11) {
        return fail("порядок байтов bdaddr не такой, как ожидалось");
    }
    if (json_escape("а\"б\\в\nг") != "а\\\"б\\\\в\\nг") {
        return fail("экранирование JSON испорчено");
    }
    std::printf("{\"ok\": true, \"channel\": %d, \"bdaddr_size\": %zu}\n",
                kRfcommChannel, sizeof(bdaddr_t));
    return 0;
}

}  // namespace

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
        int seconds = argc > 2 ? std::atoi(argv[2]) : 5;
        return cmd_scan(seconds);
    }
    std::fprintf(stderr, "неизвестная команда: %s\n", command.c_str());
    return 2;
}
