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
