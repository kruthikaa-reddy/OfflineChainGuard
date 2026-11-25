
const CACHE_NAME = "offlinechainguard-v2";
const OFFLINE_PAGE = "/offline-page.html";


// INSTALL

self.addEventListener("install", function(event) {

    event.waitUntil(

        caches.open(CACHE_NAME).then(function(cache) {

            return cache.add(OFFLINE_PAGE);

        })

    );

    self.skipWaiting();

});


// ACTIVATE

self.addEventListener("activate", function(event) {

    event.waitUntil(

        caches.keys().then(function(cacheNames) {

            return Promise.all(

                cacheNames.map(function(cacheName) {

                    if (
                        cacheName.startsWith("offlinechainguard-") &&
                        cacheName !== CACHE_NAME
                    ) {

                        return caches.delete(cacheName);

                    }

                })

            );

        })

    );

    self.clients.claim();

});


// FETCH

self.addEventListener("fetch", function(event) {

    const request = event.request;

    // Only handle GET requests
    if (request.method !== "GET") {
        return;
    }

    // Handle page navigation
    if (request.mode === "navigate") {

        event.respondWith(

            fetch(request).then(function(response) {

                // Cache the Create Transaction page
                if (
                    response.ok &&
                    new URL(request.url).pathname === "/create_transaction"
                ) {

                    const responseCopy = response.clone();

                    caches.open(CACHE_NAME).then(function(cache) {

                        cache.put(request, responseCopy);

                    });

                }

                return response;

            }).catch(function() {

                return caches.match(request).then(function(cachedPage) {

                    if (cachedPage) {
                        return cachedPage;
                    }

                    return caches.match(OFFLINE_PAGE);

                });

            })

        );

    }

});
