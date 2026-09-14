// mscap_java.js — odposlech Mistral appky na JAVA vrstve (OkHttp / React Native).
//
// Proc ne SSL_write: appka jede pres HTTP/2 a OkHttp pouziva HPACK s Huffmanovym
// kodem, takze cesta (/api/trpc/...) v plaintextu VUBEC neni. Na Java vrstve
// (okhttp3.Request, NetworkingModule) mame metodu, URL i telo jako String.
//
// Posila do hostu: metoda, url, hlavicky, telo.

function s(x) { try { return (x === null || x === undefined) ? '' : String(x); } catch (e) { return ''; } }

Java.perform(function () {
  var sent = 0;
  function report(method, url, headers, body, src) {
    if (!url || url.indexOf('http') !== 0) return;
    sent++;
    send({ src: src, method: s(method), url: s(url),
           headers: headers || {}, body: s(body) });
  }

  // --- 1) React Native NetworkingModule (fetch/XHR z JS) ---
  try {
    var NM = Java.use('com.facebook.react.modules.network.NetworkingModule');
    NM.sendRequest.overload('java.lang.String', 'java.lang.String', 'int',
                            'com.facebook.react.modules.network.Headers',
                            'okhttp3.RequestBody', 'java.lang.String',
                            'boolean', 'int', 'boolean').implementation =
      function (method, url, rid, headers, body, rt, inc, timeout, creds) {
        var h = {};
        try {
          var names = headers.names();
          for (var i = 0; i < names.size(); i++) {
            var k = names.get(i);
            h[s(k)] = s(headers.get(k));
          }
        } catch (e) {}
        var b = '';
        try { b = s(body); } catch (e) {}
        report(method, url, h, b, 'NetworkingModule');
        return this.sendRequest(method, url, rid, headers, body, rt, inc, timeout, creds);
      };
    send({ info: 'NetworkingModule hooked' });
  } catch (e) { send({ info: 'NetworkingModule: ' + e }); }

  // --- 2) okhttp3.Request (vsechny OkHttp cally) ---
  try {
    var Req = Java.use('okhttp3.Request');
    var build = Java.use('okhttp3.Request$Builder').build;
    build.implementation = function () {
      var r = build.call(this);
      var h = {};
      try {
        var names = r.headers().names();
        var it = names.iterator();
        while (it.hasNext()) { var k = it.next(); h[s(k)] = s(r.header(k)); }
      } catch (e) {}
      var b = '';
      try {
        var rb = r.body();
        if (rb) {
          var Buf = Java.use('okio.Buffer');
          var buf = Buf.$new();
          rb.writeTo(buf);
          b = s(buf.readUtf8());
        }
      } catch (e) { b = '(body err: ' + e + ')'; }
      report(r.method(), s(r.url()), h, b, 'okhttp.Request');
      return r;
    };
    send({ info: 'okhttp3.Request$Builder hooked' });
  } catch (e) { send({ info: 'okhttp: ' + e }); }
});
