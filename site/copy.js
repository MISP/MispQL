// Copy buttons on statements, shell commands and requests; none where the clipboard is unavailable.
if (navigator.clipboard) {
  document.querySelectorAll('pre.mql, pre.shell, pre.config, pre.request').forEach(function (pre) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'copy';
    button.textContent = 'Copy';
    button.addEventListener('click', function () {
      navigator.clipboard.writeText(pre.querySelector('code').textContent).then(function () {
        button.textContent = 'Copied';
        setTimeout(function () { button.textContent = 'Copy'; }, 1500);
      });
    });
    pre.appendChild(button);
  });
}
