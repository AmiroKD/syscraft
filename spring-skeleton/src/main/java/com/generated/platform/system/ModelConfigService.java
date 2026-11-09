package com.generated.platform.system;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.generated.platform.admin.entity.ModelConfigEntity;
import com.generated.platform.admin.mapper.ModelConfigMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * 模型接入配置（契约 2.2 的 model / model/test）。
 *
 * <p>「测试连接」不是可有可无的按钮：没有它，用户填错 Key 要到下一次对话才暴露，
 * 而那时的报错是「生成失败」，指不到根因（契约 2.2）。</p>
 *
 * <p>Key 的生命周期：PUT 时为空表示不修改（界面上只回显尾 4 位）；
 * 明文只在 test 请求的内存里存在一瞬间，落库永远是密文。</p>
 */
@Service
public class ModelConfigService {

    private static final Logger log = LoggerFactory.getLogger(ModelConfigService.class);

    private final ModelConfigMapper configMapper;
    private final SecretBox secretBox;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    public ModelConfigService(ModelConfigMapper configMapper, SecretBox secretBox) {
        this.configMapper = configMapper;
        this.secretBox = secretBox;
    }

    public record ModelConfig(String provider, String baseUrl, String model,
                              boolean hasKey, String keyTail) {
    }

    public record ModelSaveRequest(String provider, String baseUrl, String model, String apiKey) {
    }

    public record TestResult(boolean ok, long latencyMs, String modelEcho, String error) {
    }

    /** 供界面回显：Key 打码为尾 4 位，避免同屏泄露 */
    public ModelConfig get() {
        ModelConfigEntity config = requireConfig();
        String key = secretBox.decrypt(config.getApiKeyEnc());
        return new ModelConfig(config.getProvider(), config.getBaseUrl(), config.getModel(),
                key != null && !key.isBlank(),
                key == null || key.length() < 4 ? null : key.substring(key.length() - 4));
    }

    public void save(ModelSaveRequest request) {
        ModelConfigEntity config = requireConfig();
        if (request.provider() != null && !request.provider().isBlank()) {
            config.setProvider(request.provider().trim());
        }
        if (request.baseUrl() != null && !request.baseUrl().isBlank()) {
            config.setBaseUrl(trimTrailingSlash(request.baseUrl().trim()));
        }
        if (request.model() != null && !request.model().isBlank()) {
            config.setModel(request.model().trim());
        }
        // Key 为空 = 不修改（契约 2.2）：界面上没重填就不动旧密文
        if (request.apiKey() != null && !request.apiKey().isBlank()) {
            config.setApiKeyEnc(secretBox.encrypt(request.apiKey().trim()));
        }
        configMapper.updateById(config);
        log.info("模型接入配置已更新：{} {} {}", config.getProvider(), config.getBaseUrl(), config.getModel());
    }

    /** 用已保存的配置发一次最小请求。任何失败都返回 ok=false + 人话原因，不抛 500 */
    public TestResult test() {
        ModelConfigEntity config = requireConfig();
        String apiKey = secretBox.decrypt(config.getApiKeyEnc());
        if (apiKey == null || apiKey.isBlank()) {
            return new TestResult(false, 0, null, "尚未配置 API Key");
        }

        long start = System.currentTimeMillis();
        try {
            String payload = objectMapper.writeValueAsString(java.util.Map.of(
                    "model", config.getModel(),
                    "messages", java.util.List.of(java.util.Map.of("role", "user", "content", "ping")),
                    "max_tokens", 1));

            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(config.getBaseUrl() + "/chat/completions"))
                    .timeout(Duration.ofSeconds(15))
                    .header("Authorization", "Bearer " + apiKey)
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(payload))
                    .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            long latency = System.currentTimeMillis() - start;

            JsonNode body = objectMapper.readTree(response.body());
            if (response.statusCode() != 200) {
                String message = body.path("error").path("message").asText(null);
                return new TestResult(false, latency, null,
                        message == null ? "HTTP " + response.statusCode() : message);
            }
            // 不同网关回显字段位置不一，逐层兜底取 model
            String modelEcho = body.path("model").asText(
                    body.path("choices").path(0).path("model").asText(config.getModel()));
            return new TestResult(true, latency, modelEcho, null);
        } catch (Exception e) {
            return new TestResult(false, System.currentTimeMillis() - start, null, e.getMessage());
        }
    }

    private ModelConfigEntity requireConfig() {
        ModelConfigEntity config = configMapper.selectById(1);
        if (config == null) {
            throw new BizException(ErrorCode.SERVER_ERROR, "adm_model_config 缺少单行记录，请重放 init.sql");
        }
        return config;
    }

    private String trimTrailingSlash(String url) {
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }
}
