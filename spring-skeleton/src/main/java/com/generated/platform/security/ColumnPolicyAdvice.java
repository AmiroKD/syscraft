package com.generated.platform.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.PageResult;
import org.springframework.core.MethodParameter;
import org.springframework.http.MediaType;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 业务响应的字段裁剪出口。只拦 /api/biz/**：平台接口（admin/meta/system）不吐业务数据，
 * 生成的 VO 字段集合也不归它管。
 *
 * <p>为什么用 ResponseBodyAdvice 而不是在生成的 Service 里裁：
 * 裁剪规则随时可改（改 adm_column_policy 即生效），而生成物不会重渲染；
 * 把「策略执行」放在 platform，模板就永远不用知道策略这回事。</p>
 *
 * <p>裁剪必须在写响应前<b>重建</b>载荷：VO 经 Jackson 转成 Map（@JsonProperty 保证键是列名），
 * 裁剪后的 Map 组装回 PageResult 再返回。直接改 Map 副本对 VO 无效——那是两份内存。</p>
 */
@RestControllerAdvice
public class ColumnPolicyAdvice implements ResponseBodyAdvice<Object> {

    private static final Pattern BIZ_PATH = Pattern.compile("^/api/biz/([^/]+)/");

    private final FieldPolicyService fieldPolicyService;
    private final ObjectMapper objectMapper;

    public ColumnPolicyAdvice(FieldPolicyService fieldPolicyService, ObjectMapper objectMapper) {
        this.fieldPolicyService = fieldPolicyService;
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean supports(MethodParameter returnType, Class converterType) {
        return true;
    }

    @Override
    @SuppressWarnings("unchecked")
    public Object beforeBodyWrite(Object body, MethodParameter returnType, MediaType selectedContentType,
                                  Class selectedConverterType, ServerHttpRequest request,
                                  ServerHttpResponse response) {
        if (!(body instanceof ApiResponse<?> apiResponse) || apiResponse.data() == null) {
            return body;
        }
        Matcher matcher = BIZ_PATH.matcher(request.getURI().getPath());
        if (!matcher.find()) {
            return body;
        }
        String entity = matcher.group(1);

        Object data = apiResponse.data();
        if (data instanceof PageResult<?> page) {
            List<Map<String, Object>> rows = toMaps(page.rows());
            fieldPolicyService.apply(entity, rows);
            PageResult<Map<String, Object>> trimmed = new PageResult<>(
                    rows, page.total(), page.page(), page.size(), page.truncated());
            return new ApiResponse<>(apiResponse.code(), apiResponse.msg(), trimmed);
        }
        if (data instanceof List<?> rows) {
            // aggregate 的返回是 List<Map>，分组列与别名同样过一遍策略
            List<Map<String, Object>> maps = (List<Map<String, Object>>) rows;
            fieldPolicyService.apply(entity, maps);
            return new ApiResponse<>(apiResponse.code(), apiResponse.msg(), maps);
        }
        return body;
    }

    private List<Map<String, Object>> toMaps(List<?> rows) {
        return rows.stream()
                .map(row -> (Map<String, Object>) objectMapper.convertValue(row, LinkedHashMap.class))
                .toList();
    }
}
