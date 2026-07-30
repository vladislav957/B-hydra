// bhydra_bridge.cpp — CLI поверх bhydra_secure.hpp.
//
// Двойное назначение:
//  1) мост для pytest: каждая команда печатает результат в hex, а тест сверяет
//     его с Python байт-в-байт (тот же приём, что tests/js_bridge.js);
//  2) живой клиент и сервер: `serve` и `connect` говорят по настоящему
//     шифрованному каналу с узлом на Python.
//
// Сборка: g++ -O2 -std=c++17 -pthread -I cpp -o bhydra_bridge cpp/bhydra_bridge.cpp
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <thread>
#include <vector>

#include "bhydra_secure.hpp"

using namespace bhydra;

static Bytes arg_bytes(const char *text) {
    std::string value(text ? text : "");
    if (value.empty()) return Bytes();
    Bytes out = from_hex(value);
    if (out.empty() && !value.empty()) {
        std::fprintf(stderr, "негодный hex: %s\n", value.c_str());
        std::exit(2);
    }
    return out;
}

static int cmd_serve(int port, const U256 &identity, int rounds) {
    int listener = ::socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    ::setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = inet_addr("127.0.0.1");
    address.sin_port = htons((uint16_t)port);
    if (::bind(listener, (sockaddr *)&address, sizeof(address)) != 0) {
        std::perror("bind");
        return 1;
    }
    ::listen(listener, 4);
    std::printf("ready %s\n", to_hex(public_key_bytes(identity)).c_str());
    std::fflush(stdout);
    int fd = ::accept(listener, nullptr, nullptr);
    if (fd < 0) return 1;
    Bytes first = recv_message(fd);
    Session session = server_handshake(fd, first, identity);
    for (int i = 0; i < rounds; ++i) {
        Bytes frame = recv_message(fd);
        if (frame.empty()) break;
        Bytes request = session.decrypt(frame);
        // Эхо с приставкой: доказывает, что расшифровали ровно то, что послали.
        Bytes response(request);
        const char *prefix = "cpp:";
        response.insert(response.begin(), prefix, prefix + 4);
        send_message(fd, session.encrypt(response));
    }
    ::close(fd);
    ::close(listener);
    return 0;
}

static int cmd_connect(const char *host, int port, const std::string &expect,
                       const std::vector<std::string> &messages) {
    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = inet_addr(host);
    address.sin_port = htons((uint16_t)port);
    if (::connect(fd, (sockaddr *)&address, sizeof(address)) != 0) {
        std::perror("connect");
        return 1;
    }
    Session session = client_handshake(fd, expect);
    std::printf("peer %s\n", session.peer_key().c_str());
    for (const std::string &text : messages) {
        Bytes payload(text.begin(), text.end());
        send_message(fd, session.encrypt(payload));
        Bytes frame = recv_message(fd);
        if (frame.empty()) {
            std::printf("closed\n");
            break;
        }
        Bytes answer = session.decrypt(frame);
        std::printf("%s\n", std::string(answer.begin(), answer.end()).c_str());
    }
    ::close(fd);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "команды: sha512 shake256 hmac pub sign verify ecdh "
                     "derive frame unframe serve connect selftest\n");
        return 2;
    }
    std::string command = argv[1];
    try {
        if (command == "sha512" && argc >= 3) {
            std::printf("%s\n", to_hex(sha512(arg_bytes(argv[2]))).c_str());
        } else if (command == "shake256" && argc >= 4) {
            std::printf("%s\n", to_hex(shake256(arg_bytes(argv[2]),
                                                (size_t)std::atol(argv[3]))).c_str());
        } else if (command == "hmac" && argc >= 4) {
            std::printf("%s\n", to_hex(hmac_sha512(arg_bytes(argv[2]),
                                                   arg_bytes(argv[3]))).c_str());
        } else if (command == "pub" && argc >= 3) {
            std::printf("%s\n",
                        to_hex(public_key_bytes(U256::from_hex(argv[2]))).c_str());
        } else if (command == "sign" && argc >= 4) {
            std::printf("%s\n", to_hex(sign(U256::from_hex(argv[2]),
                                            arg_bytes(argv[3]))).c_str());
        } else if (command == "verify" && argc >= 5) {
            bool ok = verify(arg_bytes(argv[2]), arg_bytes(argv[3]),
                             arg_bytes(argv[4]));
            std::printf("%s\n", ok ? "ok" : "bad");
        } else if (command == "ecdh" && argc >= 4) {
            std::printf("%s\n", to_hex(shared_secret(U256::from_hex(argv[2]),
                                                     arg_bytes(argv[3]))).c_str());
        } else if (command == "derive" && argc >= 4) {
            SessionKeys keys = derive_keys(arg_bytes(argv[2]), arg_bytes(argv[3]));
            std::printf("%s %s %s %s\n", to_hex(keys.c2s).c_str(),
                        to_hex(keys.s2c).c_str(), to_hex(keys.mac_c2s).c_str(),
                        to_hex(keys.mac_s2c).c_str());
        } else if (command == "keystream" && argc >= 5) {
            std::printf("%s\n", to_hex(keystream(arg_bytes(argv[2]),
                                                 std::strtoull(argv[3], nullptr, 10),
                                                 (size_t)std::atol(argv[4]))).c_str());
        } else if (command == "frame" && argc >= 5) {
            // Кадр с произвольного номера: ключи задаются напрямую, чтобы тест
            // мог сверить шифртекст с Python без рукопожатия.
            SessionKeys keys;
            keys.c2s = keys.s2c = arg_bytes(argv[2]);
            keys.mac_c2s = keys.mac_s2c = arg_bytes(argv[3]);
            Session session(keys, true);
            session.set_sequence(std::strtoull(argv[4], nullptr, 10), 0);
            std::printf("%s\n", to_hex(session.encrypt(arg_bytes(argv[5]))).c_str());
        } else if (command == "unframe" && argc >= 5) {
            SessionKeys keys;
            keys.c2s = keys.s2c = arg_bytes(argv[2]);
            keys.mac_c2s = keys.mac_s2c = arg_bytes(argv[3]);
            Session session(keys, true);
            uint64_t seq = std::strtoull(argv[4], nullptr, 10);
            session.set_sequence(0, seq);
            std::printf("%s\n", to_hex(session.decrypt(arg_bytes(argv[5]))).c_str());
        } else if (command == "serve" && argc >= 4) {
            int rounds = argc >= 5 ? std::atoi(argv[4]) : 4;
            return cmd_serve(std::atoi(argv[2]), U256::from_hex(argv[3]), rounds);
        } else if (command == "connect" && argc >= 4) {
            std::string expect = argc >= 5 ? argv[4] : "";
            std::vector<std::string> messages;
            for (int i = 5; i < argc; ++i) messages.push_back(argv[i]);
            if (messages.empty()) messages.push_back("ping");
            return cmd_connect(argv[2], std::atoi(argv[3]), expect, messages);
        } else if (command == "bench") {
            // Замер внутри процесса: запуск программы стоит дороже самих
            // операций, и измерять их через отдельные вызовы бессмысленно.
            auto now = [] {
                return (double)std::clock() / CLOCKS_PER_SEC;
            };
            U256 priv = U256::from_hex(
                "f109bffc35c74e113cfcfeadba9d0e8db647b290abb1b5744240153ca7436c34");
            Bytes message(64, 0x5a);
            const int rounds = 200;
            double t0 = now();
            for (int i = 0; i < rounds; ++i) public_key_bytes(priv);
            std::printf("scalar_mul %.3f\n", (now() - t0) / rounds * 1000);
            t0 = now();
            for (int i = 0; i < rounds; ++i) sign(priv, message);
            std::printf("sign %.3f\n", (now() - t0) / rounds * 1000);
            Bytes pub = public_key_bytes(priv), sig = sign(priv, message);
            t0 = now();
            for (int i = 0; i < rounds; ++i) verify(pub, message, sig);
            std::printf("verify %.3f\n", (now() - t0) / rounds * 1000);
            SessionKeys keys = derive_keys(Bytes(32, 0x11), Bytes{'t'});
            Session session(keys, true);
            Bytes payload(4u * 1024u * 1024u, 0xa5);
            t0 = now();
            Bytes frame = session.encrypt(payload);
            double spent = now() - t0;
            std::printf("encrypt4mib %.3f %.1f\n", spent * 1000, 4.0 / spent);
        } else if (command == "bench-handshake") {
            // Рукопожатие целиком, внутри одного процесса через socketpair:
            // измерять его через запуск отдельной программы бессмысленно —
            // fork+exec стоит дороже самой криптографии.
            int rounds = argc >= 3 ? std::atoi(argv[2]) : 20;
            U256 identity = U256::from_hex(
                "f109bffc35c74e113cfcfeadba9d0e8db647b290abb1b5744240153ca7436c34");
            std::string expect = to_hex(public_key_bytes(identity));
            auto wall = [] {
                timespec ts{};
                clock_gettime(CLOCK_MONOTONIC, &ts);
                return ts.tv_sec + ts.tv_nsec / 1e9;
            };
            double spent = 0;
            for (int i = 0; i < rounds; ++i) {
                int pair[2];
                if (::socketpair(AF_UNIX, SOCK_STREAM, 0, pair) != 0) return 1;
                double t0 = wall();
                // Серверная половина — в отдельном потоке: обе стороны ждут
                // друг друга, в одном потоке это была бы взаимоблокировка.
                std::thread server([&] {
                    try {
                        Session s = server_handshake(pair[1], recv_message(pair[1]),
                                                     identity);
                        (void)s;
                    } catch (...) {
                    }
                });
                Session client = client_handshake(pair[0], expect);
                server.join();
                spent += wall() - t0;
                (void)client;
                ::close(pair[0]);
                ::close(pair[1]);
            }
            std::printf("handshake %.3f\n", spent / rounds * 1000);
        } else if (command == "selftest") {
            // Контрольные векторы: битая сборка не должна «работать».
            if (to_hex(shake256(Bytes(), 32)) !=
                "46b9dd2b0ba88d13233b3feb743eeb243fcd52ea62b81b82b50c27646ed5762f")
                throw std::runtime_error("SHAKE-256 неверен");
            Point g = generator();
            if (!on_curve(g)) throw std::runtime_error("генератор не на кривой");
            U256 priv = U256::from_hex(
                "0000000000000000000000000000000000000000000000000000000000000002");
            Bytes message{'a', 'b', 'c'};
            if (!verify(public_key_bytes(priv), message, sign(priv, message)))
                throw std::runtime_error("своя подпись не проверяется");
            std::printf("ok\n");
        } else {
            std::fprintf(stderr, "неизвестная команда или мало аргументов\n");
            return 2;
        }
    } catch (const std::exception &error) {
        std::fprintf(stderr, "ошибка: %s\n", error.what());
        return 1;
    }
    return 0;
}
