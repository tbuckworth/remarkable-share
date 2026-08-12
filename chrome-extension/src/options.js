// No configuration needed — rmapi handles auth via ~/.config/rmapi/rmapi.conf
document.addEventListener("DOMContentLoaded", () => {
  document.body.innerHTML = `
    <div style="padding: 20px; font-family: system-ui; max-width: 400px;">
      <h2>Send to reMarkable</h2>
      <p>No configuration needed. The extension uses your local <code>rmapi</code> installation.</p>
      <p>If uploads fail, check that <code>rmapi</code> is working:</p>
      <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px;">rmapi ls /</pre>
    </div>
  `;
});
