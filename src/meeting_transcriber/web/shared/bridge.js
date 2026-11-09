// How a page talks to the app: one message at a time, as JSON (see Surface in bar/surfaces.py).
  function post(msg) { window.webkit.messageHandlers.bridge.postMessage(JSON.stringify(msg)); }
