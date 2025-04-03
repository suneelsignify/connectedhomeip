import logging

import chip.clusters as Clusters
from chip.exceptions import ChipStackError
from chip.interaction_model import InteractionModelError, Status
from chip.testing.matter_testing import MatterBaseTest, TestStep, async_test_body, default_matter_test_main, type_matches
from mobly import asserts


class TC_Batch_all_clustur(MatterBaseTest):

    def steps_TC_Batch_all_clustur(self) -> list[TestStep]:
        steps = [TestStep(1, "Get remote node's MaxPathsPerInvoke", is_commissioning=True),
                 TestStep(2, "Sending `MaxPathsPerInvoke + 1` InvokeRequest if it fits into single MTU"),
                 TestStep(3, "Sending two InvokeRequests with identical paths"),
                 TestStep(4, "Sending two InvokeRequests with unique paths, but identical CommandRefs"),
                 TestStep(5, "Verify DUT responds to InvokeRequestMessage containing two valid paths"),
                 TestStep(6, "Verify DUT responds to InvokeRequestMessage containing one valid paths, and one InvokeRequest to unsupported endpoint"),
                 TestStep(7, "Verify DUT responds to InvokeRequestMessage containing two valid paths. One of which requires timed invoke, and TimedRequest in InvokeResponseMessage set to true, but never sending preceding Timed Invoke Action"),
                 TestStep(8, "Verify DUT responds to InvokeRequestMessage containing two valid paths. One of which requires timed invoke, and TimedRequest in InvokeResponseMessage set to true"),
                 TestStep(9, "Verify DUT supports extended Data Model Testing feature in General Diagnostics Cluster"),
                 TestStep(10, "Verify DUT has TestEventTriggersEnabled attribute set to true in General Diagnostics Cluster"),
                 TestStep(11, "Verify DUT capable of responding to request with multiple InvokeResponseMessages")]
        return steps

    @async_test_body
    async def test_Batch_all_clustur(self):
        dev_ctrl = self.default_controller
        dut_node_id = self.dut_node_id

        self.print_step(0, "Commissioning - already done")

        # self.step(1)
        session_parameters = dev_ctrl.GetRemoteSessionParameters(dut_node_id)
        max_paths_per_invoke = session_parameters.maxPathsPerInvoke

        asserts.assert_greater_equal(max_paths_per_invoke, 1, "Max Paths per Invoke less than spec min")
        asserts.assert_less_equal(max_paths_per_invoke, 65535, "Max Paths per Invoke greater than spec max")

        # self.step(2)
        # In practice, it was noticed that we could only fit 57 commands before we hit the MTU limit as a result we
        # conservatively try putting up to 100 commands into an Invoke request. We are expecting one of 2 things to
        # happen if max_paths_per_invoke + 1 is greater than what cap_for_batch_commands is set to:
        # 1. Client (TH) fails to send command, since we cannot fit all the commands single MTU.
        #    When this happens we get ChipStackError with CHIP_ERROR_NO_MEMORY. Test step is skipped
        #    as per test spec instructions
        # 2. Client (TH) able to send command. While unexpected we will hit two different test failure depending on
        #    what the server does.
        #    a. Server successfully handle command and send InvokeResponse with results of all individual commands
        #       being failure. In this case, test already will fail on unexpected successes.
        #    b. Server fails to handle command that is between cap_for_batch_commands and max_paths_per_invoke + 1.
        #       In this case, test fails as device should have actually succeeded and been caught in 2.a.
        cap_for_batch_commands = 300
        number_of_commands_to_send = min(max_paths_per_invoke + 1, cap_for_batch_commands)

        # Use a valid (according to MEI definition) command-id (in this case belonging to Test Vendor MC with prefix 0xfff4)
        # which should never be existing on a prodiction device. We use this decodable id to prevent hitting issues with the
        # specification being not clearly defined in respect to decoding vs processing the invoke requests.
        invalid_command_id = 0xfff4_00ff

        list_of_commands_to_send = []
        for endpoint_index in range(number_of_commands_to_send):
            # Using Toggle command to form the base as it is a command that doesn't take
            # any arguments, this allows us to fit as more requests into single MTU.
            invalid_command = Clusters.OnOff.Commands.Toggle()
            # This is how we make the command invalid
            invalid_command.command_id = invalid_command_id

            list_of_commands_to_send.append(Clusters.Command.InvokeRequestInfo(endpoint_index, invalid_command))
        print(list_of_commands_to_send, "*************commands list",
              dut_node_id, len(list_of_commands_to_send), max_paths_per_invoke)

        asserts.assert_greater_equal(len(list_of_commands_to_send), 2,
                                     "Step 2 is always expected to try sending at least 2 command, something wrong with test logic")
        try:
            await dev_ctrl.TestOnlySendBatchCommands(dut_node_id, list_of_commands_to_send, remoteMaxPathsPerInvoke=number_of_commands_to_send)
            # This might happen after TCP is enabled and DUT supports TCP. See comment above `cap_for_batch_commands`
            # for more information.
            asserts.assert_not_equal(number_of_commands_to_send, cap_for_batch_commands,
                                     "Test needs to be updated! Soft cap `cap_for_batch_commands` used in test is no longer correct")
            asserts.fail(
                f"Unexpected success return from sending too many commands, we sent {number_of_commands_to_send}, test capped at {cap_for_batch_commands}")
        except InteractionModelError as e:
            # This check is for 2.b, mentioned above. If this assert occurs, test likely needs updating. Although DUT
            # is still going to fail since it seemingly is failing to process a smaller number then it is reporting
            # that it is capable of processing.
            asserts.assert_equal(number_of_commands_to_send, max_paths_per_invoke + 1,
                                 "Test didn't send as many command as max_paths_per_invoke + 1, likely due to MTU cap_for_batch_commands, but we still got an error from server. This should have been a success from server")
            asserts.assert_equal(e.status, Status.InvalidAction,
                                 "DUT sent back an unexpected error, we were expecting InvalidAction")
            logging.info("DUT successfully failed to process `MaxPathsPerInvoke + 1` InvokeRequests")
        except ChipStackError as e:
            chip_error_no_memory = 0x0b
            asserts.assert_equal(e.err, chip_error_no_memory, "Unexpected error while trying to send InvokeRequest")
            # TODO it is possible we want to confirm DUT can handle up to MTU max. But that is not in test plan as of right now.
            # Additionally CommandSender is not currently set up to enable caller to fill up to MTU. This might be coming soon,
            # just that it is not supported today.
            logging.info("DUTs reported MaxPathsPerInvoke + 1 is larger than what fits into MTU. Test step is skipped")

        if max_paths_per_invoke == 1:
            self.skip_all_remaining_steps(3)
        else:
            asserts.assert_true('NO_OF_BATCH_COMMANDS' in self.matter_test_config.global_test_params,
                                "NO_OF_BATCH_COMMANDS must be included on the command line in "
                                "the --hex-arg flag as NO_OF_BATCH_COMMANDS:<value>, "
                                "e.g. --hex-arg NO_OF_BATCH_COMMANDS:300")

            await self.remaining_batch_commands_test_steps(False)


if __name__ == "__main__":
    default_matter_test_main()
