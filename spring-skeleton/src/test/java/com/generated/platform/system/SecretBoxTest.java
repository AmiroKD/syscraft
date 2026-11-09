package com.generated.platform.system;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * SecretBox 的纯逻辑单测：加解密回环、随机 IV、防篡改、密钥缺失拒绝。
 * 不起 Spring 上下文——这类密码学组件的语义用最快的方式钉死。
 */
class SecretBoxTest {

    private static final String SECRET = "unit-test-secret";

    private SecretBox box() {
        return new SecretBox(SECRET);
    }

    @Test
    void encryptThenDecryptRoundTrips() {
        SecretBox box = box();
        String cipher = box.encrypt("sk-test-1234567890");
        assertNotNull(cipher);
        assertEquals("sk-test-1234567890", box.decrypt(cipher));
    }

    @Test
    void samePlaintextYieldsDifferentCiphertexts() {
        // 每次加密换随机 IV：同明文两次密文必须不同，库比对无从下手（类注释的承诺）
        SecretBox box = box();
        assertNotEquals(box.encrypt("same-key"), box.encrypt("same-key"));
    }

    @Test
    void differentSecretCannotDecrypt() {
        // 密文绑定密钥：换一把 APP_SECRET，解密必须失败而不是还原出错误明文
        SecretBox box = box();
        String cipher = box.encrypt("secret-value");
        SecretBox other = new SecretBox("another-secret");
        assertThrows(RuntimeException.class, () -> other.decrypt(cipher));
    }

    @Test
    void tamperedCiphertextIsRejected() {
        // GCM 的 tag 校验：翻转密文中间一个字节，解密必须失败（防拖库篡改）
        SecretBox box = box();
        String cipher = box.encrypt("secret-value");
        byte[] raw = java.util.Base64.getDecoder().decode(cipher);
        raw[raw.length / 2] ^= 0x01;
        String tampered = java.util.Base64.getEncoder().encodeToString(raw);
        assertThrows(RuntimeException.class, () -> box.decrypt(tampered));
    }

    @Test
    void unicodeSurvivesRoundTrip() {
        SecretBox box = box();
        String value = "中文密钥-🔑-emoji";
        assertEquals(value, box.decrypt(box.encrypt(value)));
    }

    @Test
    void blankSecretIsRejectedAtConstruction() {
        assertThrows(IllegalStateException.class, () -> new SecretBox(""));
        assertThrows(IllegalStateException.class, () -> new SecretBox(null));
    }

    @Test
    void emptyPlaintextRoundTrips() {
        SecretBox box = box();
        String cipher = box.encrypt("");
        assertTrue(cipher != null && !cipher.isBlank());
        assertEquals("", box.decrypt(cipher));
    }
}
