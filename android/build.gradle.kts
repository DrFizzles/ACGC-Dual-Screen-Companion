// AGP 9.3 is the newest line whose minimum Gradle (9.5.0) matches the cached Gradle 9.5.1.
// AGP 9 compiles Kotlin itself ("built-in Kotlin"); the kotlin-android plugin is only put on the
// classpath (not applied) to raise the bundled KGP 2.2.10 to a release that supports running on JDK 25.
plugins {
    id("com.android.application") version "9.3.3" apply false
    id("org.jetbrains.kotlin.android") version "2.3.21" apply false
}
