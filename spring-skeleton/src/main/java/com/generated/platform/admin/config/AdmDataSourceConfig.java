package com.generated.platform.admin.config;

import com.zaxxer.hikari.HikariDataSource;
import org.apache.ibatis.session.SqlSessionFactory;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;

import javax.sql.DataSource;

/**
 * adm 库数据源（用户、角色、授权、审计、快照）——<b>主数据源</b>。
 *
 * <p>权限库物理名是 adm 不是 sys：MySQL 自带 sys 库，无法占用。</p>
 */
@Configuration
@MapperScan(basePackages = "com.generated.platform.admin.mapper", sqlSessionFactoryRef = "admSqlSessionFactory")
public class AdmDataSourceConfig {

    @Primary
    @Bean
    @ConfigurationProperties("spring.datasource.adm")
    public DataSource admDataSource() {
        return new HikariDataSource();
    }

    @Primary
    @Bean
    public SqlSessionFactory admSqlSessionFactory(@Qualifier("admDataSource") DataSource dataSource) throws Exception {
        return MybatisSessionFactories.create(dataSource);
    }

    /** 平台状态的单行更新用得上；两个数据源都不在时 JdbcTemplate 自动配置也会退出，必须显式给 */
    @Primary
    @Bean
    public JdbcTemplate admJdbcTemplate(@Qualifier("admDataSource") DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }
}
