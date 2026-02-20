# Research: Cloudera EncryptTool for Apache Flink

## 1. What EncryptTool Actually Does

### Overview

`org.apache.flink.util.encrypttool.EncryptTool` is a **Cloudera-proprietary** utility
shipped with Cloudera Streaming Analytics (CSA). It is **not open source** and is
**not part of Apache Flink**. The class lives under the `org.apache.flink` package
namespace but is distributed only in Cloudera's commercial Flink distribution.

### API Surface

```java
// Singleton factory - requires Flink Configuration to locate the master key
EncryptTool instance = EncryptTool.getInstance(Configuration config);

// Decrypt an encrypted config value
String plaintext = instance.decrypt(String encryptedValue);
```

### CLI Commands (`flink-encrypt-tool`)

| Action | Purpose |
|--------|---------|
| `generate-key` | Generate a per-user master key (default: saved to HDFS home) |
| `encrypt` | Encrypt a configuration property value |

### Configuration Properties

| Property | Description |
|----------|-------------|
| `security.encrypt-tool.enabled` | `true` to enable automatic decryption |
| `security.encrypt-tool.key.location` | Override master key path (e.g. `hdfs:///user/alice/myencryptionkey`) |

### Protected Properties (all-or-nothing when enabled)

All nine SSL password properties must be encrypted if any are:
- `security.ssl.internal.{truststore,keystore,key}-password`
- `security.ssl.{truststore,keystore,key}-password`
- `security.ssl.rest.{truststore,keystore,key}-password`

### Usage Pattern in flink-cyber

From `flink-cyber/flink-common/src/main/java/com/cloudera/cyber/flink/Utils.java`:

```java
// 1. Check if a key is sensitive
boolean sensitive = isSensitive(key, params);

// 2. Conditionally decrypt
String value = sensitive ? decrypt(params.get(key)) : params.get(key);

// 3. decrypt() delegates to EncryptTool singleton
public static String decrypt(String input) {
    return EncryptTool.getInstance(getConfiguration()).decrypt(input);
}
```

### Licensing

- **Not open source.** Distributed as part of Cloudera's commercial CDP/CSA product.
- Uses the `org.apache.flink` package prefix despite not being ASF-licensed code.
- The algorithm details (likely AES with a master key envelope) are not publicly documented.
- There is no indication it has ever been proposed for Apache Flink upstream.

---

## 2. Apache Flink Upstream: Existing Config Encryption / Secret SPI

### Current State: No Config Value Encryption in Apache Flink

Apache Flink has **no built-in mechanism** for encrypting/decrypting configuration values.
The closest existing features are:

#### a) Sensitive Key Masking (`GlobalConfiguration.isSensitive()`)

Flink masks values in logs and the Web UI for keys containing any of these substrings:
`password`, `secret`, `fs.azure.account.key`, `apikey`, `auth-params`, `service-key`,
`token`, `basic-auth`, `jaas.config`, `http-headers`.

This is **display-only masking**, not encryption.

#### b) FLIP-161: Configuration through Environment Variables (Flink 1.15+)

Allows overriding config values via environment variables, enabling integration with
K8s Secrets:
```yaml
env:
  - name: FLINK_CONF_security.ssl.keystore-password
    valueFrom:
      secretKeyRef:
        name: flink-secrets
        key: keystore-password
```

This avoids storing secrets in config files but does not encrypt them.

#### c) FLIP-272: Delegation Token Framework (Flink 1.17+)

SPI-based framework for obtaining/distributing/renewing authentication tokens.
Uses `DelegationTokenProvider` loaded via ServiceLoader.

```java
public interface DelegationTokenProvider {
    String serviceName();
    void init(Configuration configuration) throws Exception;
    boolean delegationTokensRequired() throws Exception;
    ObtainedDelegationTokens obtainDelegationTokens() throws Exception;
}
```

This handles **runtime credential distribution** (Kerberos tickets, HDFS tokens) but
is **not designed for config value decryption**.

#### d) FLIP-529: Connections in Flink SQL (Accepted, Jan 2026)

Introduces `ReadableSecretStore` / `WritableSecretStore` interfaces for Flink SQL
connections:

```java
interface ReadableSecretStore {
    Map<String, String> getSecret(String secretId) throws SecretNotFoundException;
}

interface WritableSecretStore {
    String storeSecret(Map<String, String> secretData);
    void removeSecret(String secretId);
    void updateSecret(String secretId, Map<String, String> secretData);
}
```

Configured via:
```yaml
table.secret-store.kind: azure-key-vault
table.secret-store.azure-key-vault.vault.url: https://vault.azure.net/
```

This is the **most relevant upstream effort**, but it is scoped to **Flink SQL
table connections only**, not general config value decryption.

#### e) SecurityModule / SecurityModuleFactory SPI

General-purpose security plugin SPI:
```java
interface SecurityModuleFactory {
    SecurityModule createModule(SecurityConfiguration securityConfig);
}
interface SecurityModule {
    void install() throws SecurityInstallException;
    void uninstall() throws SecurityInstallException;
}
```

This is for installing security contexts (JAAS, Kerberos), not config decryption.

---

## 3. Java Ecosystem Patterns

### Hadoop CredentialProvider API (`org.apache.hadoop.security.alias`)

The closest analog in the Hadoop ecosystem. Uses provider URI schemes:
- `jceks://SCHEME/path-to-keystore` (Java Keystore)
- `localjceks://file/path` (local filesystem)

Resolution path: `Configuration.getPassword(alias)` queries providers in order,
falls back to clear-text config values.

Flink does NOT integrate with Hadoop CredentialProvider for its own config values
(only for Hadoop filesystem access).

### Jasypt (Java Simplified Encryption)

Spring ecosystem standard. Uses `ENC(...)` wrapper syntax:
```yaml
spring.datasource.password: ENC(G6N9oUD9gY+yxOfgrmWnRg==)
```

Decrypted automatically by Spring Boot's `EncryptablePropertyResolver`.
Not directly usable in Flink without custom integration.

### Confluent Platform Secrets

Uses envelope encryption (AES/GCM/NoPadding) with a master passphrase:
```bash
confluent secret file encrypt --config-file server.properties
```

Confluent Manager for Flink stores encryption keys in K8s Secrets.
This is also proprietary/commercial.

---

## 4. What a Minimal Stub Would Look Like

### Approach: `ConfigValueDecryptor` SPI

A minimal SPI that plugs into `Configuration.getString()` / `Configuration.get()`:

```java
package org.apache.flink.configuration;

/**
 * SPI for decrypting configuration values at read time.
 * Loaded via ServiceLoader. If no implementation is found,
 * values are returned as-is.
 */
@Experimental
public interface ConfigValueDecryptor {

    /**
     * Initialize the decryptor with the Flink configuration.
     * Called once during GlobalConfiguration loading.
     */
    void init(Configuration configuration) throws Exception;

    /**
     * Check if a config value appears to be encrypted
     * (e.g., starts with "ENC(" or a specific prefix).
     */
    boolean isEncrypted(String value);

    /**
     * Decrypt an encrypted config value.
     * @return the decrypted plaintext value
     */
    String decrypt(String encryptedValue) throws Exception;
}
```

### Stub Implementation (pass-through for Apache Flink)

```java
package org.apache.flink.configuration;

/**
 * No-op implementation that returns values unchanged.
 * Replace with actual implementation for encrypted configs.
 */
public class NoOpConfigValueDecryptor implements ConfigValueDecryptor {
    @Override
    public void init(Configuration configuration) {}

    @Override
    public boolean isEncrypted(String value) {
        return false;
    }

    @Override
    public String decrypt(String encryptedValue) {
        return encryptedValue;
    }
}
```

### Integration Point in `Configuration.java`

```java
// In Configuration.getString() or getRawValue():
private Optional<Object> getRawValue(String key) {
    Object value = confData.get(key);
    if (value instanceof String && decryptor != null && decryptor.isEncrypted((String) value)) {
        try {
            return Optional.of(decryptor.decrypt((String) value));
        } catch (Exception e) {
            throw new RuntimeException("Failed to decrypt config value for key: " + key, e);
        }
    }
    return Optional.ofNullable(value);
}
```

### Jasypt-Based Implementation

```java
public class JasyptConfigValueDecryptor implements ConfigValueDecryptor {
    private static final String PREFIX = "ENC(";
    private static final String SUFFIX = ")";
    private StandardPBEStringEncryptor encryptor;

    @Override
    public void init(Configuration configuration) {
        encryptor = new StandardPBEStringEncryptor();
        encryptor.setPassword(configuration.getString(
            ConfigOptions.key("security.config.encryption.password")
                .stringType().noDefaultValue()));
        encryptor.setAlgorithm("PBEWithHMACSHA512AndAES_256");
    }

    @Override
    public boolean isEncrypted(String value) {
        return value != null && value.startsWith(PREFIX) && value.endsWith(SUFFIX);
    }

    @Override
    public String decrypt(String encryptedValue) {
        String ciphertext = encryptedValue.substring(PREFIX.length(),
            encryptedValue.length() - SUFFIX.length());
        return encryptor.decrypt(ciphertext);
    }
}
```

---

## 5. Feasibility of an Upstream PR

### Assessment: Moderately Feasible, but Requires a FLIP

**Arguments in favor:**
- FLIP-529 already introduced `SecretStore` for SQL connections, showing community
  appetite for secret management
- Confluent and Cloudera both built proprietary solutions, indicating real user demand
- Hadoop's `CredentialProvider` API is well-established precedent
- FLIP-161 (env var config) was partly motivated by K8s Secrets, showing awareness
  of the problem space

**Arguments against / challenges:**
- The Flink community has historically preferred external secret management (K8s
  Secrets, vault injection) over in-process decryption
- Adding decryption to `Configuration.get()` has performance and complexity implications
- FLIP-529's `SecretStore` was deliberately scoped to SQL connections; the community
  may resist broadening it
- No existing JIRA issue or FLIP proposes general config value decryption

**Recommended approach:**
1. File a JIRA issue proposing a `ConfigValueDecryptor` SPI
2. Reference FLIP-529's SecretStore, Cloudera's EncryptTool, and Confluent's solution
   as prior art
3. Keep the proposal minimal: an SPI + no-op default + Jasypt reference implementation
4. Propose it alongside Flink 2.0 configuration improvements
5. Alternatively, implement it as a Flink plugin jar that monkey-patches Configuration
   via SecurityModule, avoiding any upstream changes

### Immediate Path for flink-cyber

The existing merge conflict in `Utils.java` shows the practical problem: the `develop`
branch uses `EncryptTool` (Cloudera-proprietary), while the current branch has it
commented out. A viable immediate solution:

1. Resolve the merge conflict with a pluggable `ConfigValueDecryptor` interface
2. Provide a no-op default for Apache Flink builds
3. Provide a Jasypt-based implementation as an alternative
4. If running on Cloudera CDP, the Cloudera EncryptTool can be wired in as another
   implementation

---

## Sources

- [Cloudera CSA EncryptTool Documentation](https://docs-archive.cloudera.com/csa/1.4.0/security/topics/csa-encrypt-tool-security.html)
- [Cloudera flink-tutorials Utils.java](https://github.com/cloudera/flink-tutorials/blob/master/flink-secure-tutorial/src/main/java/com/cloudera/streaming/examples/flink/Utils.java)
- [FLIP-529: Connections in Flink SQL (SecretStore)](https://cwiki.apache.org/confluence/display/FLINK/FLIP-529:+Connections+in+Flink+SQL+and+TableAPI)
- [FLIP-272: Delegation Token Framework](https://cwiki.apache.org/confluence/display/FLINK/FLIP-272:+Generalized+delegation+token+support)
- [FLIP-161: Configuration through Environment Variables](https://cwiki.apache.org/confluence/display/FLINK/FLIP-161:+Configuration+through+envrionment+variables)
- [FLINK-14047: Hide secret values in Web UI](https://issues.apache.org/jira/browse/FLINK-14047)
- [Hadoop CredentialProvider API Guide](https://hadoop.apache.org/docs/stable/hadoop-project-dist/hadoop-common/CredentialProviderAPI.html)
- [Confluent Flink Data Encryption](https://docs.confluent.io/platform/current/flink/installation/encryption.html)
- [Apache Flink SecurityOptions.java](https://github.com/apache/flink/blob/master/flink-core/src/main/java/org/apache/flink/configuration/SecurityOptions.java)
- [Flink DelegationTokenProvider.java (local source)](thirdparty/flink/flink-core/src/main/java/org/apache/flink/core/security/token/DelegationTokenProvider.java)
