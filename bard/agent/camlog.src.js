// Loguje VSECHNA volani CamcorderProfile.hasProfile (a vraci false, aby to nespadlo).
import Java from 'frida-java-bridge';
setImmediate(function () {
  Java.perform(function () {
    var CP = Java.use('android.media.CamcorderProfile');
    var n = 0;
    CP.hasProfile.overload('int', 'int').implementation = function (cameraId, quality) {
      n++;
      send({ n: n, cameraId: cameraId, quality: quality });
      return false;
    };
    send({ ready: true });
  });
});
