# Fix: Datagen jobs failing due to missing Hadoop transitive deps

## Problem

After `restart:clean`, both datagen jobs (Java and Python) were stuck in "Skipped" state because `flink-jobmanager` crashed immediately with:

```
NoClassDefFoundError: org/apache/commons/configuration2/Configuration
  at DefaultMetricsSystem.<init>
  at UserGroupInformation.<clinit>
```

The cascade: flink-jobmanager (exit code 1, no restart policy) -> flink-taskmanager (Skipped) -> both datagen jobs (Skipped).

## Root Cause

The `flink-bootstrap` process in `devenv.nix` was cherry-picking individual Hadoop JARs from the Gradle cache into `$FLINK_HOME/lib/`:
- `hadoop-common-3.4.1.jar`
- `hadoop-auth-3.4.1.jar`
- `hadoop-shaded-guava-1.4.0.jar`
- `woodstox-core-6.5.1.jar`
- `stax2-api-4.2.1.jar`

But `hadoop-common-3.4.1` has ~20 runtime transitive deps. The cherry-picking approach was fundamentally incomplete and each fix exposed the next missing dep.

## Fix

Replaced the individual JARs with Apache's official shaded Hadoop client pair:
- `hadoop-client-api-3.4.1.jar` (19MB) - unshaded public API classes
- `hadoop-client-runtime-3.4.1.jar` (30MB) - shaded transitive deps under `org.apache.hadoop.shaded.*`

These are designed for embedding Hadoop in applications without leaking transitive dependencies. Version 3.4.1 matches the Hadoop version declared in the Iceberg submodule's `gradle/libs.versions.toml`.

Resolution uses a standalone Gradle build (`thirdparty/hadoop-client/build.gradle`) invoked via the Iceberg submodule's `gradlew`. This works on fresh clones — no pre-existing Gradle/Maven cache required.

Also added a `flink:rebuild-lib` devenv task that captures the complete clean rebuild procedure (Iceberg connectors + Hadoop client + class-level verification).

## Files Changed

- `devenv.nix`: flink-bootstrap process - Gradle-based Hadoop client resolution
- `devenv.nix`: tasks section - added `flink:rebuild-lib` task
- `thirdparty/hadoop-client/build.gradle` - standalone Gradle build for dependency resolution
- `thirdparty/hadoop-client/settings.gradle` - Gradle project settings

## Fresh Clone Flow

```
git clone ... && git submodule update --init --recursive
devenv up   # or devenv tasks run restart:clean
```

The `flink-bootstrap` process handles:
1. Build Flink 1.20.1 from `thirdparty/flink` (Maven)
2. Build Iceberg connectors from `thirdparty/iceberg` (Gradle)
3. Install S3 filesystem plugin to `plugins/`
4. Resolve + install `hadoop-client-api` + `hadoop-client-runtime` via `thirdparty/hadoop-client` (Gradle)
5. Install PyFlink JAR

## Verification

After `restart:clean`, both datagen jobs reached RUNNING state within 30 seconds:
- Java datagen: `insert-into_cybersec.default.cloudtrail_events` (100 rows/sec)
- Python datagen: `insert-into_iceberg_catalog.cybersec.cloudtrail_events` (10 rows/sec)

`flink:rebuild-lib` task also tested independently and includes class-level verification.
