import logging
import os
import pprint
import shutil
import subprocess

from .zap import MODULE_DIR
from .zap import Zap
from scanners import State
from scanners.path_translators import make_mapping_for_scanner

CLASSNAME = "ZapFlatpak"


pp = pprint.PrettyPrinter(indent=4)


class ZapFlatpak(Zap):
    ###############################################################
    # PRIVATE CONSTANTS                                           #
    # Accessed by ZapFlatpak only                               #
    ###############################################################

    ###############################################################
    # PROTECTED CONSTANTS                                         #
    # Accessed by parent Zap object                               #
    ###############################################################

    def __init__(self, config):
        """Initialize all vars based on the config.
        The code of the function only deals with the "no container" layer, the "ZAP" layer is handled by super()
        """

        logging.debug("Initializing a local instance of the ZAP scanner")
        super().__init__(config)

        # Setup defaults specific to "no container" mode
        self.config.set(
            "scanners.zap.container.parameters.executable", "zap", overwrite=False
        )

        # prepare the host <-> container mapping: flatpak container shares most directory with host
        temp_dir = self._create_work_dir()
        policies_dir = (
            f"{os.environ['HOME']}/.ZAP/policies"  # Flatpak is supposed only on Linux
        )

        self.path_map = make_mapping_for_scanner(
            "Zap",
            ("workdir", temp_dir, temp_dir),
            ("scripts", f"{MODULE_DIR}/scripts", f"{MODULE_DIR}/scripts"),
            ("policies", policies_dir, policies_dir),
        )

    ###############################################################
    # PUBLIC METHODS                                              #
    # Accessed by RapiDAST                                        #
    # + MUST be implemented                                       #
    # + SHOUT call super().<method>                               #
    # + list: setup(), run(), postprocess(), cleanup()            #
    ###############################################################

    def setup(self):
        """Prepares everything:
        - the command line to run
        - environment variables
        - files & directory

        The code of the function only deals with the "no container" layer, the "ZAP" layer is handled by super()
        """

        if self.state != State.UNCONFIGURED:
            raise RuntimeError(f"ZAP setup encounter an unexpected state: {self.state}")

        super().setup()

        # Flatpak uses the real user's home directory, so we need to use ~/.ZAP/policies/
        if self.config.get("scanners.zap.activeScan", default=False) is not False:
            policy = self.config.get(
                "scanners.zap.activeScan.policy", default="API-scan-minimal"
            )
            self._include_file(
                host_path=f"{MODULE_DIR}/policies/{policy}.policy",
                dest_in_container=self.path_map.policies.container_path,
            )

        if self.state != State.ERROR:
            self.state = State.READY

    def run(self):
        """If the state is READY, run the final run command on the local machine
        There is no need to call super() here.
        """
        logging.info("Running up the ZAP scanner in flatpak")
        if not self.state == State.READY:
            raise RuntimeError("[ZAP SCANNER]: ERROR, not ready to run")

        if self.config.get("scanners.zap.miscOptions.updateAddons", default=True):
            logging.info("Zap: Updating addons")
            if self._run_in_flatpak(["-cmd", "-addonupdate"]).returncode:
                logging.warning("ZAP addon update failed")

        # Now the real run
        logging.info(f"Running ZAP with the following command:\n{self.zap_cli}")
        # note: we need to pop out the first element (`zap`) as it is implicitly called by flatpak
        result = self._run_in_flatpak(self.zap_cli[1:])
        logging.debug(
            f"ZAP returned the following:\n=====\n{pp.pformat(result)}\n====="
        )

        # Zap's return codes : https://www.zaproxy.org/docs/desktop/addons/automation-framework/
        if result.returncode in [0, 2]:
            # 0: ZAP returned correctly. 2: ZAP returned warning
            logging.info(
                f"The ZAP process finished with no errors, and exited with code {result.returncode}"
            )
            self.state = State.DONE
        else:
            # 1: Zap hit an error
            logging.warning(
                f"The ZAP process did not finish correctly, and exited with code {result.returncode}"
            )
            self.state = State.ERROR

    def postprocess(self):
        logging.info("Running postprocess for the ZAP Host environment")
        if not self.state == State.DONE:
            raise RuntimeError(
                "No post-processing as ZAP has not successfully run yet."
            )

        super().postprocess()

        if not self.state == State.ERROR:
            self.state = State.PROCESSED

    def cleanup(self):
        logging.info("Running cleanup for the ZAP Host environment")

        if not self.state == State.PROCESSED:
            raise RuntimeError("No cleanning up as ZAP did not processed results.")

        logging.debug(f"Deleting temp directory {self._host_work_dir()}")
        shutil.rmtree(self._host_work_dir())

        super().cleanup()

        if not self.state == State.ERROR:
            self.state = State.CLEANEDUP

    ###############################################################
    # PROTECTED METHODS                                           #
    # Accessed by Zap parent only                                 #
    # + MUST be implemented                                       #
    ###############################################################

    def _add_env(self, key, value=None):
        """Environment variable to be added to the container.
        If value is None, then the value should be taken from the current host

        In "no container" type, simply add the environment in the python process
        It will be copied over to ZAP.
        If `value` is None, then do nothing, as it means it's already set in
        python's environment
        """
        if value is not None:
            os.environ[key] = value

    ###############################################################
    # PRIVATE   METHODS                                           #
    # Accessed by ZapFlatpak only                                 #
    ###############################################################

    def _run_in_flatpak(self, command):
        """A Private method: wrapper around the flatpak command
        `command` MUST be an iterable of all command options
        """
        flat = ["flatpak", "run"]
        # Share the `workdir` with flatpak
        flat.append(f"--filesystem={self.path_map.workdir.host_path}")
        flat.append("org.zaproxy.ZAP")

        flat.extend(command)

        result = subprocess.run(flat, check=False)
        logging.debug(f"Flatpak: command {flat} exited with code {result.returncode}")
        return result
