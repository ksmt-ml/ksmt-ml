plugins {
    kotlin("jvm") version "1.7.20"
    application
}

group = "io.ksmt.ml"
version = "0.1.0-SNAPSHOT"

repositories {
    mavenCentral()
}

dependencies {
    implementation("io.ksmt:ksmt-core:0.6.7")
    implementation("io.ksmt:ksmt-cvc5:0.6.7")

    // Z3 is used only for parsing SMT-LIB files.
    implementation("io.ksmt:ksmt-z3:0.6.7")

    testImplementation(kotlin("test-junit5"))
    testImplementation("org.junit.jupiter:junit-jupiter-api:5.8.2")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine:5.8.2")

}

application {
    mainClass.set("MainKt")
}

kotlin {
    jvmToolchain(17)
}

tasks.test {
    useJUnitPlatform()
}
