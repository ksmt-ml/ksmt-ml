rootProject.name = "ksmt-ml"

pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

val ksmtSourcePath = providers.gradleProperty("ksmtSourcePath").orNull

if (ksmtSourcePath != null) {
    val ksmtSourceDirectory = file(ksmtSourcePath)
    require(ksmtSourceDirectory.resolve("settings.gradle.kts").isFile) {
        "KSMT source path does not contain settings.gradle.kts: $ksmtSourcePath"
    }
    includeBuild(ksmtSourceDirectory)
}
