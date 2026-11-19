self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "show-notification" && self.registration.showNotification) {
    self.registration.showNotification(data.title || "PayShield", {
      body: data.body || "",
      icon: "/icon-192.png",
    });
  }
});
