/*
 * Copyright 2020 - 2026 Cloudera. All Rights Reserved.
 *
 * This file is licensed under the Apache License Version 2.0 (the "License"). You may not use this file
 * except in compliance with the License. You may obtain a copy of the License at
 * http://www.apache.org/licenses/LICENSE-2.0.
 *
 * This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
 * either express or implied. Refer to the License for the specific permissions and
 * limitations governing your use of the file.
 */

package com.cloudera.cyber.flink;

import org.apache.flink.configuration.Configuration;

/**
 * SPI for decrypting configuration values (passwords, secrets).
 *
 * <p>The default implementation is a no-op passthrough suitable for Apache Flink.
 * Cloudera CSA deployments can provide an implementation backed by
 * {@code org.apache.flink.util.encrypttool.EncryptTool} on the classpath.
 *
 * <p>Implementations are discovered via {@link java.util.ServiceLoader}.
 */
public interface ConfigValueDecryptor {

    /**
     * Initialize the decryptor with Flink configuration.
     */
    void init(Configuration configuration);

    /**
     * Decrypt a configuration value. If the value is not encrypted,
     * implementations should return it unchanged.
     */
    String decrypt(String value);
}
