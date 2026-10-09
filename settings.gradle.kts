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

val useLocalKsmt = providers.gradleProperty("useLocalKsmt").orNull == "true"
val localKsmtDirectory = file("../ksmt")

if (useLocalKsmt) {
    require(localKsmtDirectory.resolve("settings.gradle.kts").isFile) {
        "Local KSMT checkout was requested, but ../ksmt/settings.gradle.kts does not exist"
    }
    includeBuild(localKsmtDirectory)
}
