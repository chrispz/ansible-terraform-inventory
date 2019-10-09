#!/usr/bin/env python3
#
# @author Christian Andersen
#
# Dynamic inventory using current Terraform state to get the list of hosts related to this project.
#
# Supported resource provider: Azure
#
# This script will return public IP's if this is available.
#
# To use private IP addresses only, set environment variable ANSIBLE_INVENTORY_PRIVATE_IP=1
#

import subprocess
import json
import os


class State:
    def __init__(self, statefile: str):
       self.statefile = statefile

    def load(self) -> dict:
        if os.path.isfile(self.statefile):
            with open(self.statefile, 'r') as f:
                return json.load(f)
        else:
            return json.loads(self.get_process_output("terraform state pull -no-color"))

    @staticmethod
    def get_process_output(cmd: str) -> str:
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        data, err = process.communicate()
        if process.returncode == 0:
            return data.decode('utf-8')
        else:
            raise Exception(err.decode('utf-8'))


class AzureResource:
    def __init__(self, resource: dict):
        self.id = resource['attributes']['id']
        self.name = resource['attributes']['name']


class AzureVM(AzureResource):
    def __init__(self, resource: dict):
        super().__init__(resource)
        attr = resource['attributes']

        if len(attr['network_interface_ids']) > 0:
            self.nic_id = attr['network_interface_ids'][0]

        self.user = None
        if attr['os_profile'] and attr['os_profile'][0]['admin_username']:
            self.user = attr['os_profile'][0]['admin_username']

    def lookup(self, resources: dict):
        nics = resources['nics']
        if self.nic_id in nics:
            self.nic = nics[self.nic_id]
            self.nic.lookup(resources)

    def __repr__(self):
        return f'AzureVM({self.name}, user={self.user})'


class AzureIP(AzureResource):
    def __init__(self, resource: dict):
        super().__init__(resource)
        self.ip = resource['attributes']['ip_address']

    def __repr__(self):
        return f'AzureIP({self.name}, ip={self.ip})'


class AzureNIC(AzureResource):
    def __init__(self, resource: dict):
        super().__init__(resource)
        attr = resource['attributes']
        self.private_ip = attr['private_ip_address']
        self.public_ip = None
        if attr['ip_configuration']:
            # NOTE: use first interface, ignore the rest...
            self.public_ip_id = attr['ip_configuration'][0]['public_ip_address_id']

    def lookup(self, resources: dict):
        ips = resources['public_ip']
        if self.public_ip_id in ips:
            self.public_ip = ips[self.public_ip_id]

    def __repr__(self):
        return f'AzureNIC({self.name}, private_ip={self.private_ip})'


class Inventory:
    resource_types = {
        'azurerm_virtual_machine':      {'alias': 'hosts',     'class': AzureVM},
        'azurerm_public_ip':            {'alias': 'public_ip', 'class': AzureIP},
        'azurerm_network_interface':    {'alias': 'nics',      'class': AzureNIC}
    }

    def __init__(self, state, use_private_ip=False):
        self.resources = self.extract_resources(state)
        self.use_private_ip = use_private_ip

    @classmethod
    def extract_resources(cls, state: dict):
        resources = {}
        if 'resources' in state:
            for res in state['resources']:
                if res['type'] in cls.resource_types:
                    cls.update_resource_group(cls.resource_types[res['type']], resources, res)
        return resources

    @classmethod
    def update_resource_group(cls, typedef: dict, resources: dict, res: dict):
        name = typedef['alias']
        if name in resources:
            group = resources[name]
        else:
            group = {}
            resources[name] = group
        cls.add_resources_to_group(group, res, typedef['class'])

    @staticmethod
    def add_resources_to_group(group: dict, res: dict, restype):
        if 'instances' in res:
            for instance in res['instances']:
                resource = restype(instance)
                group[resource.id] = resource

    def add_host_to_inventory(self, inventory: dict, host: dict):
        host.lookup(self.resources)
        hostvars = {}
        inventory['_meta']['hostvars'][host.name] = hostvars
        inventory['all']['hosts'].append(host.name)

        if host.nic.public_ip and not self.use_private_ip:
            hostvars['ansible_host'] = host.nic.public_ip.ip
        else:
            hostvars['ansible_host'] = host.nic.private_ip

        if host.user:
            hostvars['ansible_user'] = host.user

    def inventory(self) -> dict:
        """Format output as Ansible inventory output"""

        inventory = {'_meta': {'hostvars': {}}, 'all': {'hosts': []}}

        if 'hosts' in self.resources:
            for host_id in self.resources['hosts']:
                self.add_host_to_inventory(inventory, self.resources['hosts'][host_id])
        return inventory


def main():
    use_private_ip = os.getenv('ANSIBLE_INVENTORY_PRIVATE_IP', '0') == '1'
    inventory = Inventory(State('terraform.tfstate').load(), use_private_ip)
    print(json.dumps(inventory.inventory(), indent=4))


if __name__ == "__main__":
    main()
