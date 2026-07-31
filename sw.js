/*
 * sw.js — сервис-воркер кошелька B-hydra.
 *
 * Делает две вещи, ради которых кошелёк вообще можно ставить на телефон:
 *
 *  1) Оболочка приложения кэшируется, поэтому кошелёк ОТКРЫВАЕТСЯ БЕЗ СЕТИ.
 *     Для «принять деньги» этого достаточно: адрес и его QR считаются на
 *     устройстве, узел для этого не нужен вовсе.
 *
 *  2) Запросы к узлу (/api/…) НИКОГДА не кэшируются. Баланс и история обязаны
 *     быть свежими: показать вчерашний баланс как сегодняшний — хуже, чем
 *     честно сказать «нет связи».
 *
 * ⚠️ Приватный ключ сюда не попадает и попасть не может: он живёт в
 * localStorage страницы, а воркер видит только запросы и ответы сети.
 */
"use strict";

const VERSION = "bhydra-v1";
const SHELL = [
  "/wallet",
  "/bhydra-sign.js",
  "/bhydra-qr.js",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
];

self.addEventListener("install", (event) => {
  // Кладём оболочку в кэш и сразу активируемся: ждать закрытия всех вкладок
  // ради обновления кошелька незачем.
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== VERSION).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;            // POST — только в сеть
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // чужие адреса не трогаем
  if (url.pathname.startsWith("/api/")) return;    // данные узла — только свежие

  // Оболочка: сначала сеть (чтобы обновления доезжали), при отказе — кэш.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then(
        (cached) => cached || caches.match("/wallet")))
  );
});
