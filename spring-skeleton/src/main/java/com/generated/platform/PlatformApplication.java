package com.generated.platform;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 骨架启动类。
 *
 * <p>依赖方向固定为 generated → platform：生成的业务代码可以调 platform 的类，
 * platform 不反向依赖 generated（禁令 D6）。</p>
 */
@SpringBootApplication
@MapperScan("com.generated.platform.admin.mapper")
public class PlatformApplication {

    public static void main(String[] args) {
        SpringApplication.run(PlatformApplication.class, args);
    }
}
