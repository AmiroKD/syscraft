package com.generated.platform.common;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.module.SimpleModule;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 时间字段宽容解析（LLM 输出格式方差在这里收口，而不是变成 400 打回模型重试）。
 *
 * <p>对话驱动的写操作里，模型最常给出的时间写法是「2026-12-31 09:00:00」（空格分隔）
 * 与 ISO 的「2026-12-31T09:00:00」两种；默认的 Jackson jsr310 反序列化只认 ISO，
 * 空格写法直接 400（实测：模型连试三种字段组合都过不了格式关）。这里统一收口：
 * 两种写法都收，输出仍保持 ISO。解析失败照常抛出——宽容针对格式，不针对语义。</p>
 */
@Configuration
public class JacksonConfig {

    private static final List<DateTimeFormatter> DATE_TIME_FORMATTERS = List.of(
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"),
            DateTimeFormatter.ISO_LOCAL_DATE_TIME,
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"),
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS"));

    private static final List<DateTimeFormatter> DATE_FORMATTERS = List.of(
            DateTimeFormatter.ISO_LOCAL_DATE,
            DateTimeFormatter.ofPattern("yyyy/MM/dd"));

    @Bean
    public org.springframework.boot.autoconfigure.jackson.Jackson2ObjectMapperBuilderCustomizer
            flexibleTimeCustomizer() {
        return builder -> builder
                .deserializerByType(LocalDateTime.class, new FlexibleDateTimeDeserializer())
                .deserializerByType(LocalDate.class, new FlexibleDateDeserializer());
    }

    /** 先按登记格式逐一尝试，都不中再退回默认解析（保持对 ISO 变体的兼容） */
    static final class FlexibleDateTimeDeserializer extends JsonDeserializer<LocalDateTime> {
        @Override
        public LocalDateTime deserialize(JsonParser parser, DeserializationContext context)
                throws IOException {
            String text = parser.getValueAsString();
            if (text == null || text.isBlank()) {
                return null;
            }
            for (DateTimeFormatter formatter : DATE_TIME_FORMATTERS) {
                try {
                    return LocalDateTime.parse(text.trim(), formatter);
                } catch (RuntimeException ignored) {
                    // 换下一个格式
                }
            }
            return LocalDateTime.parse(text.trim());
        }
    }

    static final class FlexibleDateDeserializer extends JsonDeserializer<LocalDate> {
        @Override
        public LocalDate deserialize(JsonParser parser, DeserializationContext context)
                throws IOException {
            String text = parser.getValueAsString();
            if (text == null || text.isBlank()) {
                return null;
            }
            for (DateTimeFormatter formatter : DATE_FORMATTERS) {
                try {
                    return LocalDate.parse(text.trim(), formatter);
                } catch (RuntimeException ignored) {
                    // 换下一个格式
                }
            }
            return LocalDate.parse(text.trim());
        }
    }

    /** 供 SimpleModule 场景（如有）复用 */
    static SimpleModule flexibleTimeModule() {
        SimpleModule module = new SimpleModule("flexible-time");
        module.addDeserializer(LocalDateTime.class, new FlexibleDateTimeDeserializer());
        module.addDeserializer(LocalDate.class, new FlexibleDateDeserializer());
        return module;
    }
}
