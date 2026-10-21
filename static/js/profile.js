(() => {
  const button = document.querySelector('[data-copy-account-code]');
  const code = document.getElementById('account-code');
  const status = document.getElementById('copy-code-status');
  if (!button || !code || !status) return;

  function copyWithSelection(value) {
    const field = document.createElement('textarea');
    field.value = value;
    field.readOnly = true;
    field.style.position = 'fixed';
    field.style.left = '-9999px';
    document.body.appendChild(field);
    try {
      field.focus({ preventScroll: true });
      field.select();
      field.setSelectionRange(0, value.length);
      if (!document.execCommand('copy')) throw new Error('copy unavailable');
    } finally {
      field.remove();
    }
  }

  button.hidden = false;
  button.addEventListener('click', async () => {
    const value = code.textContent.trim();
    button.disabled = true;
    try {
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('clipboard unavailable');
        await navigator.clipboard.writeText(value);
      } catch {
        copyWithSelection(value);
      }
      status.textContent = '연결코드를 복사했어요.';
    } catch {
      status.textContent = '복사하지 못했어요. 코드를 길게 누르거나 선택해 복사해 주세요.';
    } finally {
      button.disabled = false;
      button.focus({ preventScroll: true });
    }
  });
})();
