# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import json
from yaml import dump, load, Dumper, Loader

from data_validation import cli_tools, consts, jellyfish_distance
from data_validation.config_manager import ConfigManager
from data_validation.data_validation import DataValidation


def _get_arg_config_file(args):
    """Return String yaml config file path."""
    if not args.config_file:
        raise ValueError("YAML Config File was not supplied.")

    return args.config_file


def _get_yaml_config_from_file(config_file_path):
    """Return Dict of yaml validation data."""
    with open(config_file_path, "r") as yaml_file:
        yaml_configs = load(yaml_file.read(), Loader=Loader)

    return yaml_configs


def get_aggregate_config(args, config_manager):
    """Return list of formated aggregation objects.

    Args:
        config_manager (ConfigManager): Validation config manager instance.
    """
    aggregate_configs = [config_manager.build_config_count_aggregate()]

    if args.count:
        col_args = None if args.count == "*" else json.loads(args.count)
        aggregate_configs += config_manager.build_config_column_aggregates(
            "count", col_args, None
        )
    if args.sum:
        col_args = None if args.sum == "*" else json.loads(args.sum)
        aggregate_configs += config_manager.build_config_column_aggregates(
            "sum", col_args, ["int64", "float64"]
        )

    return aggregate_configs


def build_config_from_args(args, config_manager):
    """Return config manager object ready to execute.

    Args:
        config_manager (ConfigManager): Validation config manager instance.
    """
    config_manager.append_aggregates(get_aggregate_config(args, config_manager))
    if config_manager.validation_type == "GroupedColumn":
        grouped_columns = json.loads(args.grouped_columns)
        config_manager.append_query_groups(
            config_manager.build_config_grouped_columns(grouped_columns)
        )
    # TODO(GH#18): Add query filter config logic

    return config_manager


def build_config_managers_from_args(args):
    """Return a list of config managers ready to execute."""
    configs = []

    config_type = args.type
    source_conn = cli_tools.get_connection(args.source_conn)
    target_conn = cli_tools.get_connection(args.target_conn)

    result_handler_config = None
    if args.result_handler_config:
        result_handler_config = json.loads(args.result_handler_config)

    source_client = DataValidation.get_data_client(source_conn)
    target_client = DataValidation.get_data_client(target_conn)

    tables_list = json.loads(args.tables_list)
    for table_obj in tables_list:
        config_manager = ConfigManager.build_config_manager(
            config_type,
            source_conn,
            target_conn,
            source_client,
            target_client,
            table_obj,
            result_handler_config=result_handler_config,
            verbose=args.verbose,
        )
        configs.append(build_config_from_args(args, config_manager))

    return configs


def build_config_managers_from_yaml(args):
    """Returns List[ConfigManager] instances ready to be executed."""
    config_managers = []

    config_file_path = _get_arg_config_file(args)
    yaml_configs = _get_yaml_config_from_file(config_file_path)

    source_conn = cli_tools.get_connection(yaml_configs[consts.YAML_SOURCE])
    target_conn = cli_tools.get_connection(yaml_configs[consts.YAML_TARGET])

    source_client = DataValidation.get_data_client(source_conn)
    target_client = DataValidation.get_data_client(target_conn)

    for config in yaml_configs[consts.YAML_VALIDATIONS]:
        config[consts.CONFIG_SOURCE_CONN] = source_conn
        config[consts.CONFIG_TARGET_CONN] = target_conn
        config[consts.CONFIG_RESULT_HANDLER] = yaml_configs[consts.YAML_RESULT_HANDLER]
        config_manager = ConfigManager(
            config, source_client, target_client, verbose=args.verbose
        )

        config_managers.append(config_manager)

    return config_managers


def _get_all_tables_from_client(client):
    """Return a Dict of Dict objects with table info."""
    table_map = {}
    for database_name in client.list_databases():
        for table_name in client.list_tables(database=database_name):
            table_obj = {
                consts.CONFIG_SCHEMA_NAME: database_name,
                consts.CONFIG_TABLE_NAME: table_name,
            }
            table_key = "{}__{}".format(database_name, table_name)
            table_map[table_key] = table_obj

    return table_map


def _compare_match_tables(source_table_map, target_table_map):
    """Return dict config object from matching tables."""
    # TODO(dhercher): evaluate if improved comparison and score cutoffs should be used.
    table_configs = []

    target_keys = target_table_map.keys()
    for source_key in source_table_map:
        target_key = jellyfish_distance.extract_closest_match(source_key, target_keys, score_cutoff=0)
        table_config = {
            consts.CONFIG_SCHEMA_NAME: source_table_map[source_key][consts.CONFIG_SCHEMA_NAME],
            consts.CONFIG_TABLE_NAME: source_table_map[source_key][consts.CONFIG_SCHEMA_NAME],
            consts.CONFIG_TARGET_SCHEMA_NAME: target_table_map[target_key][consts.CONFIG_SCHEMA_NAME],
            consts.CONFIG_TARGET_TABLE_NAME: target_table_map[target_key][consts.CONFIG_SCHEMA_NAME],
        }
        table_configs.append(table_config)

    return table_configs


def find_tables_using_string_matching(args):
    """Return JSON String with matched tables for use in validations."""
    source_conn = cli_tools.get_connection(args.source_conn)
    target_conn = cli_tools.get_connection(args.target_conn)

    source_client = DataValidation.get_data_client(source_conn)
    target_client = DataValidation.get_data_client(target_conn)

    source_table_map = _get_all_tables_from_client(source_client)
    target_table_map = _get_all_tables_from_client(target_client)

    table_configs = _compare_match_tables(source_table_map, target_table_map)
    return json.dumps(table_configs)


def convert_config_to_yaml(args, config_managers):
    """Return dict objects formatted for yaml validations.

    Args:
        config_managers (list[ConfigManager]): List of config manager instances.
    """
    yaml_config = {
        consts.YAML_SOURCE: args.source_conn,
        consts.YAML_TARGET: args.target_conn,
        consts.YAML_RESULT_HANDLER: config_managers[0].result_handler_config,
        consts.YAML_VALIDATIONS: [],
    }

    for config_manager in config_managers:
        yaml_config[consts.YAML_VALIDATIONS].append(
            config_manager.get_yaml_validation_block()
        )

    return yaml_config


def run_validation(config_manager, verbose=False):
    """Run a single validation.

    Args:
        config_manager (ConfigManager): Validation config manager instance.
        verbose (bool): Validation setting to log queries run.
    """
    validator = DataValidation(
        config_manager.config,
        validation_builder=None,
        result_handler=None,
        verbose=verbose,
    )
    validator.execute()


def run_validations(args, config_managers):
    """Run and manage a series of validations.

    Args:
        config_managers (list[ConfigManager]): List of config manager instances.
    """
    # TODO(issue/31): Add parallel execution logic
    for config_manager in config_managers:
        run_validation(config_manager, verbose=args.verbose)


def store_yaml_config_file(args, config_managers):
    """Build a YAML config file fromt he supplied configs.

    Args:
        config_managers (list[ConfigManager]): List of config manager instances.
    """
    config_file_path = _get_arg_config_file(args)
    yaml_configs = convert_config_to_yaml(args, config_managers)
    yaml_config_str = dump(yaml_configs, Dumper=Dumper)

    with open(config_file_path, "w") as yaml_file:
        yaml_file.write(yaml_config_str)


def run(args):
    """ """
    config_managers = build_config_managers_from_args(args)

    if args.config_file:
        store_yaml_config_file(args, config_managers)
    else:
        run_validations(args, config_managers)


def run_connections(args):
    """ Run commands related to connection management."""
    if args.connect_cmd == "list":
        cli_tools.list_connections()
    elif args.connect_cmd == "add":
        conn = cli_tools.get_connection_config_from_args(args)
        # Test getting a client to validate connection details
        _ = DataValidation.get_data_client(conn)
        cli_tools.store_connection(args.connection_name, conn)
    else:
        raise ValueError(f"Connections Argument '{args.connect_cmd}' is not supported")


def main():
    # Create Parser and Get Deployment Info
    args = cli_tools.get_parsed_args()

    if args.command == "run":
        run(args)
    elif args.command == "connections":
        run_connections(args)
    elif args.command == "run-config":
        config_managers = build_config_managers_from_yaml(args)
        run_validations(args, config_managers)
    elif args.command == "find-tables":
        print(find_tables_using_string_matching(args))
    else:
        raise ValueError(f"Positional Argument '{args.command}' is not supported")


if __name__ == "__main__":
    main()
