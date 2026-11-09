package com.generated.platform.admin.config;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.config.GlobalConfig;
import com.baomidou.mybatisplus.spring.MybatisSqlSessionFactoryBean;
import org.apache.ibatis.session.SqlSessionFactory;

import javax.sql.DataSource;

/**
 * 两个数据源各建一个 SqlSessionFactory，配置必须一致，所以抽在这里。
 *
 * <p>不交给 MyBatis-Plus 自动配置：它要求容器里只有一个 DataSource，
 * 有了第二个就整体退出，结果 admin 的 Mapper 会拿到 biz 的连接去查 adm_user
 * （第一版就是这么挂的：Table 'biz.adm_user' doesn't exist）。</p>
 */
final class MybatisSessionFactories {

    private MybatisSessionFactories() {
    }

    static SqlSessionFactory create(DataSource dataSource) throws Exception {
        MybatisSqlSessionFactoryBean factory = new MybatisSqlSessionFactoryBean();
        factory.setDataSource(dataSource);

        MybatisConfiguration configuration = new MybatisConfiguration();
        configuration.setMapUnderscoreToCamelCase(true);
        factory.setConfiguration(configuration);

        GlobalConfig globalConfig = new GlobalConfig();
        globalConfig.setBanner(false);
        factory.setGlobalConfig(globalConfig);

        return factory.getObject();
    }
}
