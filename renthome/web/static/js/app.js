// Copiar al portapapeles (enlace de invitación). Sin dependencias.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const input = document.getElementById(button.dataset.copy);
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
    button.textContent = "Copiado";
  } catch (error) {
    input.select(); // el navegador no permite copiar: se deja seleccionado
    button.textContent = "Selecciona y copia";
  }
});

// Mientras hay inmobiliarias en cola, se consulta el avance y se recarga al terminar.
(() => {
  const bar = document.getElementById("rastreo");
  if (!bar || Number(bar.dataset.pending) === 0) return;
  const counter = document.getElementById("rastreo-pendientes");
  const POLL_MS = 6000;
  const poll = async () => {
    try {
      const response = await fetch(`/api/rastreo?areas=${encodeURIComponent(bar.dataset.areas)}`, { cache: "no-store" });
      if (!response.ok) return;
      const { pending } = await response.json();
      if (pending === 0) { window.location.reload(); return; }
      if (counter) counter.textContent = pending;
    } catch (error) {
      // sin conexión: se reintenta en la siguiente vuelta
    }
    window.setTimeout(poll, POLL_MS);
  };
  window.setTimeout(poll, POLL_MS);
})();
