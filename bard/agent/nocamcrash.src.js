// nocamcrash — obchazi native crash v libmedia (MediaProfiles::hasCamcorderProfile)
// tim, ze Java vrstve vratime "profil neexistuje" a nativni volani vubec neprobehne.
// Cil: proces com.google.android.googlequicksearchbox:search (Gemini UI).
import Java from 'frida-java-bridge';

setImmediate(function () {
  Java.perform(function () {
    var CP = Java.use('android.media.CamcorderProfile');
    var hits = 0;

    CP.hasProfile.overload('int', 'int').implementation = function (cameraId, quality) {
      hits++;
      if (hits <= 12) send({ hook: 'hasProfile', cameraId: cameraId, quality: quality });
      return false;   // -> nativni hasCamcorderProfile se nezavola
    };

    try {
      CP.get.overload('int', 'int').implementation = function (cameraId, quality) {
        send({ hook: 'get', cameraId: cameraId, quality: quality });
        return null;
      };
    } catch (e) { send({ warn: 'get(int,int): ' + e }); }

    send({ ready: true });
  });
});
