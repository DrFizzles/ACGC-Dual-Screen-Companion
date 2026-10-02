plugins {
    id("com.android.application")
}

android {
    namespace = "com.acdualscreen.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.acdualscreen.companion"
        minSdk = 30
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-poc"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
        }
    }
}

/**
 * Packages ../spec/ac_memory_map.json as assets/ac_memory_map.json. Only that one file is copied
 * (spec/ also holds tooling that does not belong in the APK). If the file is missing the build
 * still succeeds and the app reports "spec missing" at runtime.
 */
abstract class SpecAssetTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NAME_ONLY)
    abstract val specFile: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        out.mkdirs()
        specFile.files.filter { it.isFile }.forEach { it.copyTo(out.resolve(it.name)) }
    }
}

val specAsset = tasks.register<SpecAssetTask>("specAsset") {
    specFile.from(rootProject.file("../spec/ac_memory_map.json"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(specAsset, SpecAssetTask::outputDir)
    }
}

// RealSpecTest and CrossCheckTest read ../spec/ac_memory_map.json directly: make it a test input
// so a spec change re-runs the tests instead of leaving them UP-TO-DATE.
tasks.withType<Test>().configureEach {
    inputs.files(rootProject.file("../spec/ac_memory_map.json"))
        .withPropertyName("sharedSpec").withPathSensitivity(PathSensitivity.NONE)
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    // Real org.json for JVM unit tests (android.jar only has throwing stubs).
    testImplementation("org.json:json:20250517")
}
