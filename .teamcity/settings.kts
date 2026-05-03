import jetbrains.buildServer.configs.kotlin.*
import jetbrains.buildServer.configs.kotlin.buildFeatures.perfmon
import jetbrains.buildServer.configs.kotlin.buildSteps.script
import jetbrains.buildServer.configs.kotlin.triggers.vcs

/*
The settings script is an entry point for defining a TeamCity
project hierarchy. The script should contain a single call to the
project() function with a Project instance or an init function as
an argument.

VcsRoots, BuildTypes, Templates, and subprojects can be
registered inside the project using the vcsRoot(), buildType(),
template(), and subProject() methods respectively.

To debug settings scripts in command-line, run the

    mvnDebug org.jetbrains.teamcity:teamcity-configs-maven-plugin:generate

command and attach your debugger to the port 8000.

To debug in IntelliJ Idea, open the 'Maven Projects' tool window (View
-> Tool Windows -> Maven Projects), find the generate task node
(Plugins -> teamcity-configs -> teamcity-configs:generate), the
'Debug' option is available in the context menu for the task.
*/

version = "2025.11"

project {

    vcsRoot(Dolt)

    buildType(Bundle)
    buildType(ExportData)
    buildType(Echo)
    buildType(TestData)
    buildType(Integrate)
}

object Bundle : BuildType({
    name = "Bundle (release zip)"

    artifactRules = "endless-sky-release.zip"

    vcs {
        root(DslContext.settingsRoot, "-:.teamcity")

        checkoutMode = CheckoutMode.ON_AGENT
        cleanCheckout = true
    }

    steps {
        script {
            name = "Zip release"
            scriptContent = """
                #!/usr/bin/env bash
                set -euo pipefail
                
                # Artifact deps already populated:
                #   stage/endless-sky                 (binary, from EndlessSky_Echo)
                #   stage/data/from-dolt/*.txt        (dolt overlay, from EndlessSky_ExportData)
                # Now overlay the source-tracked game data from the VCS checkout.
                # data/from-dolt only contains .gitkeep in the repo, so cp -r merges harmlessly
                # alongside the dolt-exported .txt files already in stage/data/from-dolt.
                mkdir -p stage/data
                cp -r data/. stage/data/
                cp -r images sounds stage/
                cp credits.txt copyright keys.txt license.txt icon.png stage/
                
                chmod +x stage/endless-sky
                
                cd stage
                zip -r ../endless-sky-release.zip . >/dev/null
                cd ..
                
                echo 'Release zip:'
                ls -lh endless-sky-release.zip
                echo 'Top-level entries:'
                unzip -l endless-sky-release.zip | awk 'NR>3 && ${'$'}4 !~ "/" {print ${'$'}4}' | head -30
                echo 'Total entries:'
                unzip -l endless-sky-release.zip | tail -1
            """.trimIndent()
        }
    }

    dependencies {
        dependency(Echo) {
            snapshot {
                onDependencyFailure = FailureAction.FAIL_TO_START
            }

            artifacts {
                artifactRules = "endless-sky-binary.zip!** => stage"
            }
        }
        dependency(ExportData) {
            snapshot {
                onDependencyFailure = FailureAction.FAIL_TO_START
            }

            artifacts {
                artifactRules = "data-from-dolt.zip!** => stage/data/from-dolt"
            }
        }
    }

    requirements {
        exists("zip.path")
    }
})

object Echo : BuildType({
    name = "Build client"

    artifactRules = "build/endless-sky => endless-sky-binary.zip"

    vcs {
        root(DslContext.settingsRoot, "-:.teamcity")
    }

    steps {
        script {
            id = "simpleRunner"
            scriptContent = """
                cmake -S . -B build -G Ninja \
                  -DCMAKE_BUILD_TYPE=Debug \
                  -DES_USE_VCPKG=OFF \
                  -DCMAKE_COMPILE_WARNING_AS_ERROR=OFF \
                  -DBUILD_TESTING=OFF
                cmake --build build -t endless-sky -j 1
            """.trimIndent()
        }
    }

    features {
        perfmon {
        }
    }

    requirements {
        exists("cmake.path")
    }
})

object ExportData : BuildType({
    name = "Export Dolt data"

    artifactRules = "artifacts/data-from-dolt => data-from-dolt.zip"

    vcs {
        root(DslContext.settingsRoot, "-:.teamcity")
        root(Dolt, "+:.=>dolt-checkout")
    }

    steps {
        script {
            name = "Export Dolt data"
            scriptContent = """
                #!/usr/bin/env bash
                set -euo pipefail
                
                HOST="${Dolt.paramRefs["dolt.sqlserver.host"]}"
                PORT="${Dolt.paramRefs["dolt.sqlserver.port"]}"
                USER="${Dolt.paramRefs["dolt.sqlserver.user"]}"
                
                echo "Connecting to dolt sql-server at ${'$'}HOST:${'$'}PORT as ${'$'}USER"
                
                python3 -m pip install --quiet --break-system-packages mysql-connector-python
                
                python3 scripts/export-dolt-to-data.py \
                  --host "${'$'}HOST" --port "${'$'}PORT" --user "${'$'}USER" \
                  --schema endless-sky --out artifacts/data-from-dolt/
                
                echo 'Export complete. Files:'
                ls -la artifacts/data-from-dolt/
            """.trimIndent()
        }
    }

    features {
        feature {
            type = "dolt-sql-server"
            param("dolt.sqlserver.vcsRootId", "EndlessSky_Dolt")
        }
    }

    requirements {
        exists("dolt.path")
    }
})

object TestData : BuildType({
    name = "Test Dolt data"

    description = "Run data-quality probes against the Dolt-hosted endless-sky " +
            "database; each probe is reported as a separate test via TC service messages."

    vcs {
        root(DslContext.settingsRoot, "-:.teamcity")
        root(Dolt, "+:.=>dolt-checkout")
    }

    steps {
        script {
            name = "Run Dolt data tests"
            scriptContent = """
                #!/usr/bin/env bash
                set -euo pipefail

                HOST="${Dolt.paramRefs["dolt.sqlserver.host"]}"
                PORT="${Dolt.paramRefs["dolt.sqlserver.port"]}"
                USER="${Dolt.paramRefs["dolt.sqlserver.user"]}"

                echo "Connecting to dolt sql-server at ${'$'}HOST:${'$'}PORT as ${'$'}USER"

                python3 -m pip install --quiet --break-system-packages mysql-connector-python

                python3 scripts/test-dolt-data.py \
                  --host "${'$'}HOST" --port "${'$'}PORT" --user "${'$'}USER" \
                  --schema endless-sky
            """.trimIndent()
        }
    }

    features {
        feature {
            type = "dolt-sql-server"
            param("dolt.sqlserver.vcsRootId", "EndlessSky_Dolt")
        }
    }

    requirements {
        exists("dolt.path")
    }
})

object Integrate : BuildType({
    name = "Integrate (release bundle)"

    type = BuildTypeSettings.Type.COMPOSITE

    vcs {
        root(DslContext.settingsRoot, "-:.teamcity")
        root(Dolt, "+:.=>dolt-checkout")

        showDependenciesChanges = true
    }

    triggers {
        vcs {
            triggerRules = "-:.teamcity"
        }
    }

    dependencies {
        dependency(Bundle) {
            snapshot {
                onDependencyFailure = FailureAction.FAIL_TO_START
            }

            artifacts {
                artifactRules = "+:*"
            }
        }
        dependency(TestData) {
            snapshot {
                onDependencyFailure = FailureAction.FAIL_TO_START
            }
        }
    }
})

object Dolt : VcsRoot({
    type = "dolt"
    name = "EndlessSky Dolt DB"
    param("dolt.connectionId", "PROJECT_EXT_2")
    param("dolt.sqlserver.port", "14001")
    param("dolt.sqlserver.password", "")
    param("dolt.sqlserver.host", "127.0.0.1")
    param("dolt.url", "dolthub/endless-sky")
    param("dolt.sqlserver.user", "root")
})
