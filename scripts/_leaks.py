"""What the public packages may say about the service, checked without publishing the list.

The WebSocket and REST schemas may only define the messages in ``WS_MESSAGES`` and
``REST_MESSAGES``. Any other text may not name an operation an API key cannot call, or
an internal transport. Those tokens are held as SHA-256 digests so the guard itself
does not publish them.
"""
import hashlib
import re

WS_MESSAGES = frozenset({"Frame", "GexBookReason", "GexStrikeBook", "GexStrikeBookUpdate"})
REST_MESSAGES = frozenset({
    "BarsResponse", "Bar", "GexGridResponse", "GexGridRow", "ChainResponse", "ChainRow",
    "OffExchangeActivityResponse", "OffExchangeActivityRow",
    "OffExchangeConcentrationResponse", "OffExchangeConcentrationRow",
    "OffExchangeProfileResponse", "OffExchangeProfileRow",
    "OffExchangeCompositionResponse", "OffExchangeCompositionRow",
})

_DIGESTS = frozenset({
    "0092f1556b50bb55", "01af68d440c41694", "020b9e40ec45f127", "053bd4b086991773",
    "05484d176370eaac", "06981cdc038892f2", "09fd428b5c4083d7", "0cc4d6eabd2acbbc",
    "0e34efeebe23987c", "1316842dfb276442", "186bc18949174708", "18ac746fdcfed95d",
    "1a46319066e9df7e", "1b32904b090188fe", "1eaa293a03575163", "22766d40408ebf30",
    "26469b048fbad115", "26bb7e085a3e86c5", "27d3f96521de11f5", "2bbaa7e2e86cc221",
    "2c4d62a3ce690531", "2f0a41f774d45098", "30272708d80048ec", "352ba56a304a2e9a",
    "382b5295d2277150", "440b18e4dd16ea9e", "44366d461623cb5e", "44635481604ba14a",
    "4745797f17250f37", "47c102121efd8a3e", "49b7170fd7406b6a", "4f9fbc456d495e33",
    "50fb112f19de5561", "53338a3f1a5b0be0", "61a75a2220c788d6", "67e09fac30f31a89",
    "6840a6264ffe67ee", "6b850bdf958c8b00", "6c330fa610a95d1e", "6dc7b7febb61a6de",
    "70d92afe3cbfc731", "710b5771fd3b8c41", "72fcbb8520411a59", "730a8649626a3139",
    "75442d69b4bc6478", "7592e2c55f8ed8c8", "76f2f2baf9815480", "781cee735d88c8cf",
    "791049401d2cc32f", "7e97a4238281b71f", "7fbe3c3d968662cd", "810f24f17d5bbb93",
    "826900e458b4ab6a", "8334516f998f574c", "843ae8ca068a0ef0", "844984b89e2d820d",
    "890095c00c34a8c1", "8d31a55e3ee05e5c", "9198e41a451c8aa8", "921c244fd09b2059",
    "925fcc4b52abea99", "94184272231cefa6", "97fcda58bd735261", "9914edaefa929828",
    "99d33269823d1101", "a067c7d001de706c", "a5f7a00dac411daa", "a6cc81f3dd35c270",
    "a839ffee1226a4fe", "abe004031e65b870", "ad185f9e2d1db7f9", "adace3e0dfa8d59f",
    "ae8b7302ca690b67", "af5e6c7f85849159", "b0987afeab97e660", "b316817475894dd3",
    "b48b4d72762f26c8", "b6ec1a0281651804", "bc7e6ea46bf86828", "c4a3b3384dfe485e",
    "cac41b933806dc4e", "cb3e504a96f890c2", "cdc46bae715fb8b5", "cf822e2ae05f1327",
    "d289e63a1fcba098", "d722abf0cc0e817e", "dbb1cfc3b6af61c8", "dc6a2c8ab37d8244",
    "dcdad00cec460729", "dde3e7d5cdb5429b", "de0ae9bec08499f0", "deb4d5ccc159eb30",
    "e1c29be484cbe584", "e40553d7d72a9eb9", "e794336c6ba125d5", "e7a5adf6c8a9db3b",
    "ea4dde82989c90d0", "ec10ee737e69c379", "ed18829e50b8045d", "f0de588fc5d869d4",
    "f36903604c8a8304", "f41f185b9402f934", "f474701743966630", "f550b535cb8e1429",
    "f6fdc734447c0c07", "f73857bbcc0e6747", "f7b76892ca039bfc", "f8444bf546bf2500",
    "f8bbd4e30bc3cd6f", "fe3b0437a050db87",
})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]")
_DEFINITION = re.compile(r"^\s*(?:message|enum)\s+(\w+)", re.M)


def find_leak(text):
    """The first forbidden token in ``text``, as its digest, or None."""
    for token in _TOKEN.findall(text):
        for candidate in {token.lower(), token.lower().replace("-", "")}:
            digest = hashlib.sha256(candidate.encode()).hexdigest()[:16]
            if digest in _DIGESTS:
                return digest
    return None


def schema_extras(proto_text, allowed):
    """Messages or enums a .proto defines beyond ``allowed``."""
    return sorted(set(_DEFINITION.findall(proto_text)) - allowed)
