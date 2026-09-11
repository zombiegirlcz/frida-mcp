// probe: je v agent runtime dostupny Java bridge?
send({ t: 'log', m: 'agent loaded' });
send({ t: 'log', m: 'typeof Java = ' + (typeof Java) });
send({ t: 'log', m: 'typeof JavaBridge = ' + (typeof JavaBridge) });
send({ t: 'log', m: 'typeof ObjC = ' + (typeof ObjC) });
try {
  send({ t: 'log', m: 'Java.available = ' + Java.available });
  Java.perform(function () {
    send({ t: 'log', m: 'Java.perform OK, env=' + Java.vm.getEnv() });
    var n = 0, sample = [];
    Java.enumerateLoadedClasses({
      onMatch: function (name) {
        if (name.indexOf('okhttp3') === 0) { n++; if (sample.length < 5) sample.push(name); }
      },
      onComplete: function () {
        send({ t: 'log', m: 'okhttp3 classes = ' + n + ' sample=' + JSON.stringify(sample) });
      }
    });
  });
} catch (e) {
  send({ t: 'log', m: 'Java error: ' + e });
}
