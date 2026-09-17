"""纯 Python 的 AES-128-ECB 解密（零依赖）。

为什么不用 pycryptodome：
网易云 NCM 里只有"密钥块"（约 80 字节）和"元数据块"（几百字节）需要 AES，
音频本体走的是 RC4 变种流密码 —— AES 用量极小，纯 Python 完全够快，
同时省掉一个二进制依赖，PyInstaller 打包更干净。

S 盒按 FIPS-197 的构造法在运行时生成，而不是手抄 256 字节常量表 ——
少一个抄错一个字节就全盘失败的风险，且首次导入的开销只有几十微秒。
"""
from __future__ import annotations


def _rotl8(v: int, n: int) -> int:
    return ((v << n) | (v >> (8 - n))) & 0xFF


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _build_sbox() -> tuple[list[int], list[int]]:
    """生成 S 盒与逆 S 盒。"""
    sbox = [0] * 256
    p = q = 1
    while True:
        # p ← p · 3   （GF(2^8) 上的乘以 3）
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        # q ← q / 3   （等价于 q · 0xF6）
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        # 仿射变换
        x = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63                     # 0 没有乘法逆元，单独填
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


SBOX, INV_SBOX = _build_sbox()


def _gf_mul(a: int, b: int) -> int:
    """GF(2^8) 上的乘法（AES 多项式 0x11B）。"""
    product = 0
    for _ in range(8):
        if b & 1:
            product ^= a
        a = _xtime(a)
        b >>= 1
    return product


# 列混淆 / 逆列混淆要用的常数乘法表，预计算成查表
_MUL = {
    c: [_gf_mul(x, c) for x in range(256)]
    for c in (0x02, 0x03, 0x09, 0x0B, 0x0D, 0x0E)
}


class AES128ECB:
    """AES-128 / ECB，只实现解密方向。"""

    __slots__ = ("_rk",)

    def __init__(self, key: bytes) -> None:
        if len(key) != 16:
            raise ValueError("AES-128 需要 16 字节密钥")
        self._rk = self._expand(key)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _expand(key: bytes) -> list[list[int]]:
        """密钥扩展，产出 11 组轮密钥，每组 4 个字（每字 4 字节）。"""
        w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
        rcon = 1
        for i in range(4, 44):
            t = list(w[i - 1])
            if i % 4 == 0:
                t = [SBOX[b] for b in (t[1], t[2], t[3], t[0])]   # RotWord + SubWord
                t[0] ^= rcon
                rcon = _xtime(rcon)
            w.append([w[i - 4][j] ^ t[j] for j in range(4)])
        return [w[i * 4:(i + 1) * 4] for i in range(11)]

    # ------------------------------------------------------------------ #
    def decrypt_block(self, block: bytes) -> bytes:
        """解密单个 16 字节分组。"""
        s = list(block)                     # 列主序：s[r + 4*c]

        self._add_round_key(s, self._rk[10])
        for rnd in range(9, 0, -1):
            self._inv_shift_rows(s)
            self._inv_sub_bytes(s)
            self._add_round_key(s, self._rk[rnd])
            self._inv_mix_columns(s)
        self._inv_shift_rows(s)
        self._inv_sub_bytes(s)
        self._add_round_key(s, self._rk[0])
        return bytes(s)

    def decrypt(self, data: bytes) -> bytes:
        """解密任意 16 字节整数倍的数据。"""
        if len(data) % 16:
            raise ValueError("数据长度必须是 16 的整数倍")
        out = bytearray(len(data))
        for off in range(0, len(data), 16):
            out[off:off + 16] = self.decrypt_block(data[off:off + 16])
        return bytes(out)

    # ------------------------------------------------------------------ #
    # 加密方向：生产流程用不到，但自测要自己造样本，所以一并实现
    # ------------------------------------------------------------------ #
    def encrypt_block(self, block: bytes) -> bytes:
        s = list(block)
        self._add_round_key(s, self._rk[0])
        for rnd in range(1, 10):
            self._sub_bytes(s)
            self._shift_rows(s)
            self._mix_columns(s)
            self._add_round_key(s, self._rk[rnd])
        self._sub_bytes(s)
        self._shift_rows(s)
        self._add_round_key(s, self._rk[10])
        return bytes(s)

    def encrypt(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise ValueError("数据长度必须是 16 的整数倍")
        out = bytearray(len(data))
        for off in range(0, len(data), 16):
            out[off:off + 16] = self.encrypt_block(data[off:off + 16])
        return bytes(out)

    @staticmethod
    def _sub_bytes(s: list[int]) -> None:
        for i in range(16):
            s[i] = SBOX[s[i]]

    @staticmethod
    def _shift_rows(s: list[int]) -> None:
        # 第 r 行循环左移 r 个字节
        for r in range(1, 4):
            row = [s[r + 4 * c] for c in range(4)]
            for c in range(4):
                s[r + 4 * c] = row[(c + r) % 4]

    @staticmethod
    def _mix_columns(s: list[int]) -> None:
        m2, m3 = _MUL[0x02], _MUL[0x03]
        for c in range(4):
            i = 4 * c
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            s[i] = m2[a0] ^ m3[a1] ^ a2 ^ a3
            s[i + 1] = a0 ^ m2[a1] ^ m3[a2] ^ a3
            s[i + 2] = a0 ^ a1 ^ m2[a2] ^ m3[a3]
            s[i + 3] = m3[a0] ^ a1 ^ a2 ^ m2[a3]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _add_round_key(s: list[int], rk: list[list[int]]) -> None:
        for c in range(4):
            word = rk[c]
            base = 4 * c
            s[base] ^= word[0]
            s[base + 1] ^= word[1]
            s[base + 2] ^= word[2]
            s[base + 3] ^= word[3]

    @staticmethod
    def _inv_sub_bytes(s: list[int]) -> None:
        for i in range(16):
            s[i] = INV_SBOX[s[i]]

    @staticmethod
    def _inv_shift_rows(s: list[int]) -> None:
        # 第 r 行循环右移 r 个字节
        for r in range(1, 4):
            row = [s[r + 4 * c] for c in range(4)]
            for c in range(4):
                s[r + 4 * c] = row[(c - r) % 4]

    @staticmethod
    def _inv_mix_columns(s: list[int]) -> None:
        m9, mb, md, me = _MUL[0x09], _MUL[0x0B], _MUL[0x0D], _MUL[0x0E]
        for c in range(4):
            i = 4 * c
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            s[i] = me[a0] ^ mb[a1] ^ md[a2] ^ m9[a3]
            s[i + 1] = m9[a0] ^ me[a1] ^ mb[a2] ^ md[a3]
            s[i + 2] = md[a0] ^ m9[a1] ^ me[a2] ^ mb[a3]
            s[i + 3] = mb[a0] ^ md[a1] ^ m9[a2] ^ me[a3]


def pkcs7_unpad(data: bytes, block: int = 16) -> bytes:
    """去掉 PKCS#7 填充。"""
    if not data or len(data) % block:
        return data
    pad = data[-1]
    if 1 <= pad <= block and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data
