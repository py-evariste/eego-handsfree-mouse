"""
Interface Python du SDK eemagine eego 1.3.19 (ctypes appelle directement l'interface C de eego-SDK.dll)

Le fabricant ne fournit que des en-têtes C++ ; l'enveloppe C++ (wrapper.cc) appelle en interne
un ensemble de fonctions C eemagine_sdk_* exportées par la DLL. Ici on reproduit en Python
la même séquence d'appels que wrapper.cc :
    setup(NULL) → init() → get_amplifiers_info() → open_eeg_stream()
    → boucle prefetch() + get_data() → close_stream() → close_amplifier() → exit()

Remarque : le manuel indique que la couche C n'est « pas garantie stable », mais la version 1.3.19
n'évolue plus ; pour cet EE-410 elle est figée.

Utilisation :
    from eego import EegoSDK
    with EegoSDK(r"C:\\path\\to\\eego-SDK.dll") as sdk:
        amp = sdk.get_amplifier()
        print(amp.serial, amp.type, amp.firmware)
        with amp.open_eeg_stream(rate=500, bipolar_range=1.5) as st:
            data = st.get_data()   # tableau numpy (échantillons, voies), en V
"""

import ctypes as C
from pathlib import Path

import numpy as np

# Même ordre que eemagine_sdk_channel_type dans wrapper.h (attention : différent de l'énumération C++ de channel.h)
CHANNEL_TYPES = {
    0: "reference",
    1: "bipolar",
    2: "accelerometer",
    3: "gyroscope",
    4: "magnetometer",
    5: "trigger",
    6: "sample_counter",
    7: "impedance_reference",
    8: "impedance_ground",
}

ERRORS = {
    -1: "NOT_CONNECTED (amplificateur débranché)",
    -2: "ALREADY_EXISTS",
    -3: "NOT_FOUND (amplificateur introuvable)",
    -4: "INCORRECT_VALUE (paramètre invalide, ou données perdues : getData espacé de plus d'1 s / coupure USB)",
    -5: "UNKNOWN",
}

ALL_CHANNELS = 0xFFFFFFFFFFFFFFFF


class EegoError(RuntimeError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"Erreur eego SDK {code} {ERRORS.get(code, '')} {detail}".strip())


class _AmpInfo(C.Structure):
    _fields_ = [("id", C.c_int), ("serial", C.c_char * 64)]


class _ChanInfo(C.Structure):
    _fields_ = [("index", C.c_int), ("type", C.c_int)]


class EegoSDK:
    """Charge la DLL et initialise le SDK. Une seule instance par programme ; appeler close() à la fin (ou utiliser with)."""

    def __init__(self, dll_path):
        dll_path = Path(dll_path)
        if not dll_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {dll_path}")
        if dll_path.stat().st_size < 600_000:
            # La DLL 64 bits déjà extraite sur la clé USB ne fait que 352 Ko : elle est corrompue ; la version complète du zip fait 639 Ko
            raise RuntimeError(f"{dll_path} ne fait que {dll_path.stat().st_size} octets : fichier incomplet, ré-extraire depuis le zip")
        self.dll = C.CDLL(str(dll_path))
        self._declare()
        self.dll.eemagine_sdk_setup(None)
        self.dll.eemagine_sdk_init()
        self._open = True

    def _declare(self):
        d = self.dll
        i, dbl, p = C.c_int, C.c_double, C.POINTER
        sig = {
            "eemagine_sdk_setup": ([C.c_void_p], None),
            "eemagine_sdk_init": ([], None),
            "eemagine_sdk_exit": ([], None),
            "eemagine_sdk_get_version": ([], i),
            "eemagine_sdk_get_amplifiers_info": ([p(_AmpInfo), i], i),
            "eemagine_sdk_close_amplifier": ([i], i),
            "eemagine_sdk_get_amplifier_serial": ([i, C.c_char_p, i], i),
            "eemagine_sdk_get_amplifier_version": ([i], i),
            "eemagine_sdk_get_amplifier_type": ([i, C.c_char_p, i], i),
            "eemagine_sdk_get_amplifier_channel_list": ([i, p(_ChanInfo), i], i),
            "eemagine_sdk_get_amplifier_sampling_rates_available": ([i, p(i), i], i),
            "eemagine_sdk_get_amplifier_reference_ranges_available": ([i, p(dbl), i], i),
            "eemagine_sdk_get_amplifier_bipolar_ranges_available": ([i, p(dbl), i], i),
            "eemagine_sdk_open_eeg_stream": ([i, i, dbl, dbl, C.c_ulonglong, C.c_ulonglong], i),
            "eemagine_sdk_open_impedance_stream": ([i, C.c_ulonglong], i),
            "eemagine_sdk_close_stream": ([i], i),
            "eemagine_sdk_get_stream_channel_list": ([i, p(_ChanInfo), i], i),
            "eemagine_sdk_get_stream_channel_count": ([i], i),
            "eemagine_sdk_prefetch": ([i], i),
            "eemagine_sdk_get_data": ([i, p(dbl), i], i),
            "eemagine_sdk_get_error_string": ([C.c_char_p, i], i),
        }
        for name, (args, res) in sig.items():
            f = getattr(d, name)
            f.argtypes, f.restype = args, res

    def check(self, rv):
        """Une valeur négative renvoyée par une fonction C signale une erreur : on la convertit en exception (avec le message du SDK)."""
        if rv < 0:
            buf = C.create_string_buffer(1024)
            self.dll.eemagine_sdk_get_error_string(buf, 1024)
            raise EegoError(rv, buf.value.decode(errors="replace"))
        return rv

    @property
    def version(self):
        return self.dll.eemagine_sdk_get_version()

    def list_amplifiers(self):
        arr = (_AmpInfo * 64)()
        n = self.check(self.dll.eemagine_sdk_get_amplifiers_info(arr, 64))
        return [Amplifier(self, arr[k].id, arr[k].serial.decode(errors="replace")) for k in range(n)]

    def get_amplifier(self):
        amps = self.list_amplifiers()
        if not amps:
            raise EegoError(-3, "Aucun amplificateur détecté : vérifier le câble USB, le pilote (Gestionnaire de périphériques) et la LED de l'amplificateur")
        for extra in amps[1:]:
            extra.close()
        return amps[0]

    def close(self):
        if self._open:
            self.dll.eemagine_sdk_exit()
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _channels(arr, n):
    return [(arr[k].index, CHANNEL_TYPES.get(arr[k].type, f"?{arr[k].type}")) for k in range(n)]


class Amplifier:
    def __init__(self, sdk, amp_id, serial):
        self.sdk, self.id, self.serial = sdk, amp_id, serial
        self._open = True

    def _call(self, name, *args):
        return self.sdk.check(getattr(self.sdk.dll, name)(self.id, *args))

    @property
    def type(self):
        buf = C.create_string_buffer(1024)
        self._call("eemagine_sdk_get_amplifier_type", buf, 1024)
        return buf.value.decode(errors="replace")

    @property
    def firmware(self):
        return self._call("eemagine_sdk_get_amplifier_version")

    @property
    def channels(self):
        arr = (_ChanInfo * 1024)()
        return _channels(arr, self._call("eemagine_sdk_get_amplifier_channel_list", arr, 1024))

    def _array(self, name, ctype):
        arr = (ctype * 1024)()
        n = self._call(name, arr, 1024)
        return [arr[k] for k in range(n)]

    @property
    def sampling_rates(self):
        return self._array("eemagine_sdk_get_amplifier_sampling_rates_available", C.c_int)

    @property
    def reference_ranges(self):
        return self._array("eemagine_sdk_get_amplifier_reference_ranges_available", C.c_double)

    @property
    def bipolar_ranges(self):
        return self._array("eemagine_sdk_get_amplifier_bipolar_ranges_available", C.c_double)

    def open_eeg_stream(self, rate=500, reference_range=1.0, bipolar_range=1.5,
                        ref_mask=ALL_CHANNELS, bip_mask=ALL_CHANNELS):
        """Données en V. Un seul flux à la fois ; il faut le fermer (close) après usage."""
        sid = self._call("eemagine_sdk_open_eeg_stream", rate, reference_range, bipolar_range,
                         ref_mask, bip_mask)
        return Stream(self.sdk, sid, rate)

    def open_impedance_stream(self, ref_mask=ALL_CHANNELS):
        """Données en Ω. get_data() renvoie la dernière mesure d'impédance (1 échantillon)."""
        sid = self._call("eemagine_sdk_open_impedance_stream", ref_mask)
        return Stream(self.sdk, sid, None)

    def close(self):
        if self._open:
            self.sdk.dll.eemagine_sdk_close_amplifier(self.id)
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Stream:
    def __init__(self, sdk, stream_id, rate):
        self.sdk, self.id, self.rate = sdk, stream_id, rate
        arr = (_ChanInfo * 1024)()
        n = sdk.check(sdk.dll.eemagine_sdk_get_stream_channel_list(stream_id, arr, 1024))
        self.channels = _channels(arr, n)
        self.n_channels = n
        self._open = True

    def get_data(self):
        """Récupère tous les échantillons depuis l'appel précédent → (échantillons, voies).
        L'intervalle entre deux appels doit être < 1 s.

        Les deux dernières colonnes sont en général trigger et sample_counter (voir self.channels).
        """
        nbytes = self.sdk.check(self.sdk.dll.eemagine_sdk_prefetch(self.id))
        n_values = nbytes // 8
        if n_values == 0:
            return np.empty((0, self.n_channels))
        buf = np.empty(n_values, dtype=np.float64)
        self.sdk.check(self.sdk.dll.eemagine_sdk_get_data(
            self.id, buf.ctypes.data_as(C.POINTER(C.c_double)), nbytes))
        return buf.reshape(-1, self.n_channels)   # entrelacé par échantillon : [s0ch0, s0ch1, ..., s1ch0, ...]

    def close(self):
        if self._open:
            self.sdk.dll.eemagine_sdk_close_stream(self.id)
            self._open = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
