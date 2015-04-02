#!/usr/bin/python

# vim: tabstop=4 shiftwidth=4 softtabstop=4
import os
import sys
import re
import datetime
import commands
import json
import pdb
import subprocess
from netaddr import *
import string
import textwrap
import shutil
import random 
import tempfile
import re
import openstack_hieradata
from server_mgr_logger import ServerMgrlogger as ServerMgrlogger
from server_mgr_exception import ServerMgrException as ServerMgrException
from esxi_contrailvm import ContrailVM as ContrailVM
from contrail_defaults import *


class ServerMgrPuppet:
    _puppet_site_file_name = "site.pp"
    _site_manifest_file = ''
    _node_env_map_file = "puppet/node_mapping.json"
    # Dictionary to keep information about which parameters are already added
    # when cycling thru parameter list for all the roles.
    _params_dict = {}

    def pupp_create_site_manifest_file(self):
        self._site_manifest_file = self.puppet_directory + "manifests/" + \
            self._puppet_site_file_name
        if os.path.isfile(self._site_manifest_file):
            return
        fp = open(self._site_manifest_file, 'w')
        if not fp:
            assert 0, "puppet site config file create failed"
        fp.close()
    # end pupp_create_site_manifest_file

    def pupp_create_server_manifest_file(self, provision_params):
        version = provision_params.get('puppet_manifest_version', "")
        server_manifest_file = self.puppet_directory + "environments/contrail_" + \
            version + "/manifests/" + \
            provision_params['server_id'] + "." + \
            provision_params['domain'] + ".pp"
        if not os.path.exists(os.path.dirname(server_manifest_file)):
            os.makedirs(os.path.dirname(server_manifest_file))
        if os.path.exists(server_manifest_file):
            os.remove(server_manifest_file)
        fp = open(server_manifest_file, 'w')
        if not fp:
            assert 0, "puppet server config file create failed"
        fp.close()
        return server_manifest_file
    # end pupp_create_server_manifest_file

    def __init__(self, smgr_base_dir, puppet_dir):
        self._smgr_log = ServerMgrlogger()
        self._smgr_log.log(self._smgr_log.DEBUG, "ServerMgrPuppet Init")


        self.smgr_base_dir = smgr_base_dir
        self.puppet_directory = puppet_dir
        if not os.path.exists(os.path.dirname(puppet_dir)):
            os.makedirs(os.path.dirname(puppet_dir))

        # Check and create puppet main site file
        self.pupp_create_site_manifest_file()
    # end __init__

    def puppet_add_script_end_role(self, provision_params, last_res_added=None):
        if 'execute_script' in provision_params.keys():
            script_data = eval(provision_params["execute_script"])
            script_name = script_data["script_name"]
            script_args = script_data["args"]
            print "Executing Custom script"
            data = '''    # Execute Script for all roles.
            contrail_%s::contrail_common::contrail-exec-script{%s:
                script_name => "%s",
                args => "%s",
                require => %s
            }\n\n''' % (
                provision_params['puppet_manifest_version'],
                script_name.replace('.','_'),
                script_name,
                script_args.replace('"','\''), last_res_added)
            return data

    #API to return control interfaces IP address 
    # else return MGMT IP address
    def get_control_ip(self, provision_params, mgmt_ip):
        intf_control = {}
        """
        if 'contrail_params' in  provision_params:
            contrail_dict = eval(provision_params['contrail_params'])
            control_data_intf = contrail_dict['control_data_interface']
            if provision_params['interface_list'] and \
                     provision_params['interface_list'] [control_data_intf]:
                control_data_ip = provision_params['interface_list'] \
                                [control_data_intf] ['ip']
            if control_data_ip:
                return '"' + str(IPNetwork(control_data_ip).ip) + '"'
            else:
                return '"' + provision_params['server_ip'] + '"'
        """
        if provision_params['control_net'] [mgmt_ip]:
            intf_control = eval(provision_params['control_net'] [mgmt_ip]) 
        for intf,values in intf_control.items():
            if intf:
                return str(IPNetwork(values['ip_address']).ip)
            else:
                return provision_params['server_ip']
        return mgmt_ip
    # end get_control_ip

    def storage_get_control_network_mask(self, provision_params,
        server, cluster):
        role_ips_dict = provision_params['roles']
        cluster_params = eval(cluster['parameters'])
        server_params = eval(server['parameters'])
        #openstack_ip = cluster_params.get("internal_vip", None)
        openstack_ip = ''
        self_ip = server.get("ip_address", "")
        if openstack_ip is None or openstack_ip == '':
            if self_ip in role_ips_dict['openstack']:
                openstack_ip = self_ip
            else:
                openstack_ip = role_ips_dict['openstack'][0]

        subnet_mask = server.get("subnet_mask", "")
        if not subnet_mask:
            subnet_mask = cluster_params.get("subnet_mask", "255.255.255.0")

        subnet_address = str(IPNetwork(
            openstack_ip + "/" + subnet_mask).network)

        self._smgr_log.log(self._smgr_log.DEBUG, "control-net : %s" % str( provision_params['control_net']))
        if provision_params['control_net'] [openstack_ip]:
            intf_control = eval(provision_params['control_net'] [openstack_ip])
            self._smgr_log.log(self._smgr_log.DEBUG, "openstack-control-net : %s" % str(intf_control ))

        for intf,values in intf_control.items():
            if intf:
                self._smgr_log.log(self._smgr_log.DEBUG, "ip_address : %s" % values['ip_address'])
                return '"' + str(IPNetwork(values['ip_address']).network) + '/'+ str(IPNetwork(values['ip_address']).prefixlen) + '"'
            else:
                self._smgr_log.log(self._smgr_log.DEBUG, "server_ip : %s" % values['server_ip'])
                return '"' + str(IPNetwork(provision_params['server_ip']).network) + '/'+ str(IPNetwork(provision_params['server_ip']).prefixlen) + '"'

        return '"' + str(IPNetwork(subnet_address).network) + '/'+ str(IPNetwork(subnet_address).prefixlen) + '"'

    ## return 1.1.1.0/24 format network and mask
    def get_control_network_mask(self, provision_params, mgmt_ip_str):
        intf_control = {}
	##netaddr.IPNetwork(ip_cidr).network, netaddr.IPNetwork(ip_cidr).prefixlen
        mgmt_ip = mgmt_ip_str.strip("\"")
        if provision_params['control_net'] [mgmt_ip]:
            intf_control = eval(provision_params['control_net'] [mgmt_ip])
        for intf,values in intf_control.items():
            if intf:
        	self._smgr_log.log(self._smgr_log.DEBUG, "ip_address : %s" % values['ip_address'])
                return '"' + str(IPNetwork(values['ip_address']).network) + '/'+ str(IPNetwork(values['ip_address']).prefixlen) + '"'
            else:
        	self._smgr_log.log(self._smgr_log.DEBUG, "server_ip : %s" % values['server_ip'])
                return '"' + str(IPNetwork(provision_params['server_ip']).network) + '/'+ str(IPNetwork(provision_params['server_ip']).prefixlen) + '"'
                #return '"' + provision_params['server_ip'] + '"'
	ip_address_cidr = mgmt_ip + '/' + provision_params['subnet-mask']
        return '"' + str(IPNetwork(ip_address_cidr).network) + '/'+ str(IPNetwork(ip_address_cidr).prefixlen) + '"'
        #return '"' + mgmt_ip + '"'
    # end get_control_network_mask 

    def _update_kernel(self, provision_params):
        # Get all the parameters needed to send to puppet manifest.
        if 'kernel_upgrade' in provision_params and \
             provision_params['kernel_upgrade'].lower() == "yes" and \
            'kernel_version' in provision_params and \
            provision_params['kernel_version'] != '' :
            before_param = \
            "Contrail_%s::Contrail_common::Contrail_common[\"contrail_common\"]" %(
                        provision_params['puppet_manifest_version'])
            data = '''    # Upgrade the kernel.
        contrail_%s::contrail_common::upgrade-kernel{upgrade_kernel:
            contrail_kernel_version => "%s",
            before => %s
        }\n\n''' % (provision_params['puppet_manifest_version'],
                    provision_params['kernel_version'],
                    before_param)
            return data
        else: 
            return ''
    # end _update_kernel

    def _update_provision_start(self, provision_params):
        # Get all the parameters needed to send to puppet manifest.
        before_param = \
        "Contrail_%s::Contrail_common::Contrail-setup-repo[\"contrail_repo\"]" %(
                    provision_params['puppet_manifest_version'])
        data = '''    # Create repository config on target.
    contrail_%s::contrail_common::report_status{provision_started:
        state => "%s",
        before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                "provision_started" ,before_param)
        return data
    # end _update_provision_start

    def _update_provision_complete(self, provision_params, require_param):
        # Get all the parameters needed to send to puppet manifest.
        data = '''    # Update the state of server that provision is complete
    contrail_%s::contrail_common::report_status{provision_completed:
        state => "%s",
        require => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                "provision_completed" , require_param)
        return data
    # end _update_provision_complete

    def _update_system_config(self, provision_params):
        before_param = "Contrail_%s::Contrail_common::Contrail-setup-repo[\"contrail_repo\"]" % (
            provision_params['puppet_manifest_version'])

        data = '''    # Create system config on target.
    contrail_%s::contrail_common::contrail_setup_users_groups{contrail_repo:
        before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'], before_param)

	return data

    def _repository_config(self, provision_params):
        # Get all the parameters needed to send to puppet manifest.
        before_param = "Contrail_%s::Contrail_common::Contrail-install-repo[\"install_repo\"]" % (
            provision_params['puppet_manifest_version'])
        data = '''    # Create repository config on target.
    contrail_%s::contrail_common::contrail-setup-repo{contrail_repo:
        contrail_repo_name => "%s",
        contrail_server_mgr_ip => "%s",
        before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                provision_params['package_image_id'],
                provision_params["server_mgr_ip"], before_param)

        if 'kernel_upgrade' in provision_params['kernel_upgrade'] and \
             provision_params['kernel_upgrade'] == "yes" and \
            'kernel_version' in provision_params['kernel_version '] and \
            provision_params['kernel_version'] != '' :

            before_param = "Contrail_%s::Contrail_common::Upgrade-kernel[\"upgrade_kernel\"]" % \
                       (provision_params['puppet_manifest_version'])
        else:
            before_param = \
            "Contrail_%s::Contrail_common::Contrail_common[\"contrail_common\"]" %(
                        provision_params['puppet_manifest_version'])


        data += '''    # Install repo on target.
    contrail_%s::contrail_common::contrail-install-repo{install_repo:
        contrail_repo_type => "%s",
        before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                provision_params['package_type'],
                before_param)

	if 'storage-compute' in provision_params['host_roles'] or 'storage-master' in provision_params['host_roles']:
        	print "found"
		data += '''   # Install storage repo on target.
    contrail_%s::contrail_common::contrail-setup-repo{contrail_storage_repo:
        contrail_repo_name => "%s",
	contrail_server_mgr_ip => "%s",
	before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
		provision_params['storage_repo_id'],
		provision_params["server_mgr_ip"], before_param)


        return data

    # end _repository_config

    def create_interface(self, provision_params, last_rest_added=None):
        #add Interface Steps
        data = ''


        # Get all the parameters needed to send to puppet manifest.


        intf_bonds = {}
        intf_control = {}
        intf_data = {}
        if provision_params['intf_bond']:
            intf_bonds = eval(provision_params['intf_bond'])
        requires_cmd = ""
        require_list = []
        if provision_params['intf_control']:
            intf_control = eval(provision_params['intf_control'])
        for intf,values in intf_control.items():
            members = "\"\""
            bond_opts = ""
            if intf in intf_bonds.keys():
                bond = intf_bonds[intf]
                members = bond['member_interfaces']
                bond_opts = bond['options']
            require_cmd = "Contrail_%s::Contrail_common::Contrail-setup-interface[\"%s\"]" %(
                provision_params['puppet_manifest_version'], intf)
            require_list.append(require_cmd)
            data += '''     # Setup Interface
        contrail_%s::contrail_common::contrail-setup-interface{%s:
        contrail_device => "%s",
        contrail_members => %s,
        contrail_bond_opts => "%s",
        contrail_ip => "%s",
        contrail_gw => "%s"
        }\n\n''' % (provision_params['puppet_manifest_version'],
        intf, intf, members , bond_opts,
        values['ip_address'], values['gateway'])
            

        if provision_params['intf_data']:
            intf_data = eval(provision_params['intf_data'])
        for intf,values in intf_data.items():
            members = "\"\""
            bond_opts = ""
            if intf in intf_bonds.keys():
                bond = intf_bonds[intf]
                members = bond['member_interfaces']
                bond_opts = bond['options']
            require_cmd = "Contrail_%s::Contrail_common::Contrail-setup-interface[\"%s\"]" %(
                provision_params['puppet_manifest_version'], intf)
            require_list.append(require_cmd)
            data += '''     # Setup Interface
        contrail_%s::contrail_common::contrail-setup-interface{%s:
        contrail_device => "%s",
        contrail_members => %s,
        contrail_bond_opts => "%s",
        contrail_ip => "%s",
        contrail_gw => "%s"
        }\n\n''' % (provision_params['puppet_manifest_version'],
        intf, intf, members , bond_opts,
        values['ip_address'], values['gateway'])

        data_first = '''    # Create repository config on target.
    contrail_%s::contrail_common::contrail-setup-repo{contrail_repo:
        contrail_repo_name => "%s",
        contrail_server_mgr_ip => "%s",
        before => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                provision_params['package_image_id'],
                provision_params["server_mgr_ip"],
               '[%s]' % ','.join(map(str, require_list)))

        data = data_first + data

        data += '''    #CB to start provision_after setup_interface
        contrail_%s::contrail_common::create-interface-cb{create_interface_cb:
        contrail_package_id => "%s",
        require => %s
        }\n\n''' % (provision_params['puppet_manifest_version'],
                    provision_params['package_image_id'],
                   '[%s]' % ','.join(map(str, require_list)))

        return data

    def puppet_add_common_role(self, provision_params, last_res_added=None):
        data = ''

        rsyslong_param = {}
        rsyslog_string = "*.*"
        # Get all the parameters needed to send to puppet manifest.

        # Computing the rsyslog parameter here.
        if 'rsyslog_params' in provision_params:
            if  provision_params['rsyslog_params']['status'] == "enable":
                if 'collector' in provision_params['rsyslog_params']:
                    rsyslong_param['collector'] = provision_params['rsyslog_params']['collector']
                else :
                    rsyslong_param['collector'] = 'dynamic'

                if 'port' in provision_params['rsyslog_params']:
                    rsyslong_param['port'] = provision_params['rsyslog_params']['port']
                else :
                    rsyslong_param['port'] = "19876"

                if 'proto' in provision_params['rsyslog_params']:
                    rsyslong_param['proto'] = provision_params['rsyslog_params']['proto']
                else :
                    rsyslong_param['proto'] = 'tcp'

                # Computing rsyslog string for rsyslog.conf
                collector_servers = provision_params['roles']['collector']
                collector_ip_list_control=[]
                for itr in collector_servers:
                    collector_ip_list_control.append(self.get_control_ip(provision_params, itr))
                if rsyslong_param['proto'] == 'tcp':
                    rsyslog_string+=" "+"@@"
                else:
                    rsyslog_string+=" "+"@"

                if rsyslong_param['collector'] == 'dynamic':
                    rsyslog_string+=random.choice(collector_ip_list_control).replace('"','')   
                else:
                    rsyslog_string+=collector_ip_list_control[0].replace('"','')
                rsyslog_string+= ":" + str(rsyslong_param['port'])

                if self._params_dict.get('rsyslog_status', None) is None:
                    self._params_dict['rsyslog_status'] = (
                        "\"%s\"" %(   
                        provision_params['rsyslog_params']['status']))

                if self._params_dict.get('rsyslog_port', None) is None:
                    self._params_dict['rsyslog_port'] = (  
                        "\"%s\"" %(
                        rsyslong_param['port']))

                if self._params_dict.get('rsyslog_collector', None) is None:
                    self._params_dict['rsyslog_collector'] = (
                        "\"%s\"" %(  
                        rsyslong_param['collector']))

                if self._params_dict.get('rsyslog_proto', None) is None:
                    self._params_dict['rsyslog_proto'] = (
                        "\"%s\"" %(   
                        rsyslong_param['proto']))

                if self._params_dict.get('rsyslog_string', None) is None:
                    self._params_dict['rsyslog_string'] = (
                        "\"%s\"" %(
                        rsyslog_string))
        else:
            if self._params_dict.get('rsyslog_status', None) is None:
                    self._params_dict['rsyslog_status'] = (
                        "\"%s\"" %(   
                        "disable"))
            if self._params_dict.get('rsyslog_port', None) is None:
                    self._params_dict['rsyslog_port'] = (
                        "\"%s\"" %(
                        "-1"))

        # Build Params items
        if self._params_dict.get('self_ip', None) is None:
            self._params_dict['self_ip'] = (
                "\"%s\"" %(
                    self.get_control_ip(provision_params,
                    provision_params['server_ip'])))
        if self._params_dict.get('system_name', None) is None:
            self._params_dict['system_name'] = (
                "\"%s\"" %(
                    provision_params["server_id"]))
        # Build resource items
        data += '''    # custom type common for all roles.
    contrail_%s::contrail_common::contrail_common{contrail_common:
    }\n\n''' % (provision_params['puppet_manifest_version'])

        return data
        # end puppet_add_common_role

    def puppet_add_database_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
        config_server = provision_params['roles']['config'][0]
        database_server = provision_params['roles']['database']
        database_ip_control = self.get_control_ip(
            provision_params, provision_params['server_ip'])
        database_ip_control_list=[]
        for item in database_server:
            database_ip_control_list.append(self.get_control_ip(provision_params,item))
        
        config_server_control = self.get_control_ip(
            provision_params, config_server)
        if 'zookeeper' in provision_params['roles']:
            zk_servers = provision_params['roles']['zookeeper']
        else:
            zk_servers = []
            db_ip_list = ["\"%s\""%(x) for x in database_server]
            zoo_ip_list = ["\"%s\""%(x) for x in zk_servers]
            zk_ip_list_control=[]
            contrail_database_index = database_server.index(
                provision_params["server_ip"])+1

        #####-
        cassandra_seeds = ["\"%s\""%(x) for x in \
            provision_params['roles']['config']]
        cassandra_seeds_control_list=[]
        for item in cassandra_seeds:
            cassandra_seeds_control_list.append(self.get_control_ip(provision_params,item))
        #####-

        # Build Params items
        if self._params_dict.get(
            'contrail_database_ip', None) is None:
            self._params_dict['contrail_database_ip'] = (
                "\"%s\"" %(database_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_database_dir', None) is None:
            self._params_dict['contrail_database_dir'] = (
                "\"%s\"" %(provision_params["database_dir"]))
        if self._params_dict.get(
            'contrail_database_initial_token', None) is None:
            self._params_dict['contrail_database_initial_token'] = (
                "\"%s\"" %(provision_params["database_token"]))
        if self._params_dict.get(
            'contrail_cassandra_seeds', None) is None:
            self._params_dict['contrail_cassandra_seeds'] = (
                "[%s]" %(','.join(cassandra_seeds_control_list)))
        if self._params_dict.get(
            'system_name', None) is None:
            self._params_dict['system_name'] = (
                "\"%s\"" %(provision_params["server_id"]))
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_zookeeper_ip_list', None) is None:
            self._params_dict['contrail_zookeeper_ip_list'] = (
                "[%s]" %(','.join(database_ip_control_list)))
        if self._params_dict.get(
            'contrail_database_index', None) is None:
            self._params_dict['contrail_database_index'] = (
                "\"%s\"" %(contrail_database_index))
        # Build resource items
        data += '''    # contrail-database role.
    contrail_%s::contrail_database::contrail_database{contrail_database:
        require => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                last_res_added)
        return data
    # end puppet_add_database_role

    def puppet_add_openstack_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
#if provision_params['haproxy'] == 'enable':
 #           data += '''         #Source HA Proxy CFG
  #      contrail_common::haproxy-cfg{haproxy_cfg:
   #         server_id => "%s"}\n\n
#''' % (server["server_id"])


        if (provision_params['openstack_mgmt_ip'] == ''):
            contrail_openstack_mgmt_ip = provision_params["server_ip"]
        else:
            contrail_openstack_mgmt_ip = provision_params['openstack_mgmt_ip']
        if provision_params['server_ip'] in provision_params['roles']['config']:
            config_server = provision_params['server_ip']
        else:
            config_server = provision_params['roles']['config'][0]

        if provision_params['server_ip'] in provision_params['roles']['compute']:
            compute_server = provision_params['server_ip']
        else:
            compute_server = provision_params['roles']['compute'][0]

        contrail_openstack_mgmt_ip_control=self.get_control_ip(provision_params,contrail_openstack_mgmt_ip)
        config_server_control=self.get_control_ip(provision_params,config_server)
        compute_server_control=self.get_control_ip(provision_params,compute_server)
      
        config_servers_names = provision_params['role_ids']['config']
        # Keeping openstack index hardcoded untill ha is implemented 
        openstack_index="1"
        rabbit_user_list=[]
        for cfgm_name in config_servers_names:
            rabbit_user_list.append('rabbit@'+str(cfgm_name))
        rabbit_user_list=str(rabbit_user_list)
        rabbit_user_list=rabbit_user_list.replace(" ","")
        #Chhnadak
        amqp_server = provision_params['roles']['config'][0]
        amqp_server_control=self.get_control_ip(provision_params,amqp_server)
        #End here

        # Build Params items
        if self._params_dict.get(
            'contrail_openstack_ip', None) is None:
            self._params_dict['contrail_openstack_ip'] = (
                "\"%s\"" %(self.get_control_ip(
                    provision_params,
                    provision_params["server_ip"])))
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        #TODO Check here
        if self._params_dict.get(
            'contrail_compute_ip', None) is None:
            self._params_dict['contrail_compute_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_mgmt_ip', None) is None:
            self._params_dict['contrail_openstack_mgmt_ip'] = (
                "\"%s\"" %(contrail_openstack_mgmt_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_service_token', None) is None:
            self._params_dict['contrail_service_token'] = (
                "\"%s\"" %(provision_params["service_token"]))
        if self._params_dict.get(
            'contrail_ks_admin_passwd', None) is None:
            self._params_dict['contrail_ks_admin_passwd'] = (
                "\"%s\"" %(provision_params["keystone_password"]))
        if self._params_dict.get(
            'contrail_haproxy', None) is None:
            self._params_dict['contrail_haproxy'] = (
                "\"%s\"" %(provision_params["haproxy"]))
        if self._params_dict.get(
            'contrail_amqp_server_ip', None) is None:
            self._params_dict['contrail_amqp_server_ip'] = (
                "\"%s\"" %(amqp_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_index', None) is None:
            self._params_dict['contrail_openstack_index'] = (
                "\"%s\"" %(openstack_index.replace('"', '')))
        if self._params_dict.get(
            'contrail_rabbit_user', None) is None:
            self._params_dict['contrail_rabbit_user'] = (
                "\"%s\"" %(rabbit_user_list.replace('"', '')))
        if self._params_dict.get(
            'contrail_cfgm_number', None) is None:
            self._params_dict['contrail_cfgm_number'] = (
                "\"%s\"" %(len(config_servers_names)))

        # Build resource items
        data += '''    # contrail-openstack role.
    contrail_%s::contrail_openstack::contrail_openstack{contrail_openstack:
        require => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                last_res_added)


        if provision_params["haproxy"] == "enable":
            self.create_openstack_ha_proxy(provision_params)

        return data
    # end puppet_add_openstack_role



    def create_config_ha_proxy(self, provision_params):
        smgr_dir  = staging_dir = "/etc/puppet/environments/contrail_" + \
            provision_params['puppet_manifest_version'] + "/modules/contrail_" + \
            provision_params['puppet_manifest_version'] + "/files/"

        cfg_ha_proxy_tmpl = string.Template("""
#contrail-config-marker-start
listen contrail-config-stats :5937
   mode http
   stats enable
   stats uri /
   stats auth $__contrail_hap_user__:$__contrail_hap_passwd__

frontend quantum-server *:9696
    default_backend    quantum-server-backend

frontend  contrail-api *:8082
    default_backend    contrail-api-backend

frontend  contrail-discovery *:5998
    default_backend    contrail-discovery-backend

backend quantum-server-backend
    balance     roundrobin
$__contrail_quantum_servers__
    #server  10.84.14.2 10.84.14.2:9697 check

backend contrail-api-backend
    balance     roundrobin
$__contrail_api_backend_servers__
    #server  10.84.14.2 10.84.14.2:9100 check
    #server  10.84.14.2 10.84.14.2:9101 check

backend contrail-discovery-backend
    balance     roundrobin
$__contrail_disc_backend_servers__
    #server  10.84.14.2 10.84.14.2:9110 check
    #server  10.84.14.2 10.84.14.2:9111 check
#contrail-config-marker-end
""")
    #ha proxy for cfg
        config_role_list = provision_params['roles']['config']
        q_listen_port = 9697
        q_server_lines = ''
        api_listen_port = 9100
        api_server_lines = ''
        disc_listen_port = 9110
        disc_server_lines = ''
        smgr_dir  = staging_dir = "/etc/puppet/environments/contrail_" + \
            provision_params['puppet_manifest_version'] + "/modules/contrail_" + \
            provision_params['puppet_manifest_version'] + "/files/"
        #TODO
        nworkers = 1
        for config_host in config_role_list:
             host_ip = config_host
             host_ip_control=self.get_control_ip(provision_params,host_ip)
             n_workers = 1
             q_server_lines = q_server_lines + \
                             '    server %s %s:%s check\n' \
                             %(host_ip_control, host_ip_control, str(q_listen_port))
             for i in range(nworkers):
                api_server_lines = api_server_lines + \
                 '    server %s %s:%s check\n' \
                 %(host_ip_control, host_ip_control, str(api_listen_port + i))
                disc_server_lines = disc_server_lines + \
                 '    server %s %s:%s check\n' \
                 %(host_ip_control, host_ip_control, str(disc_listen_port + i))

        for config_host in config_role_list:
             haproxy_config = cfg_ha_proxy_tmpl.safe_substitute({
             '__contrail_quantum_servers__': q_server_lines,
             '__contrail_api_backend_servers__': api_server_lines,
             '__contrail_disc_backend_servers__': disc_server_lines,
             '__contrail_hap_user__': 'haproxy',
             '__contrail_hap_passwd__': 'contrail123',
             })

        ha_proxy_cfg = staging_dir + provision_params['server_id'] + ".cfg"
        shutil.copy2(smgr_dir + "haproxy.cfg", ha_proxy_cfg)
        cfg_file = open(ha_proxy_cfg, 'a')
        cfg_file.write(haproxy_config)
        cfg_file.close()

    def puppet_add_zk_role(self, provision_params, last_res_added):
        data = ''
        config_servers = provision_params['roles']['config']
        zk_servers = provision_params['roles']['zookeeper']

        cfgm_ip_list = ["\"%s\""%(x) for x in config_servers]
        zoo_ip_list = ["\"%s\""%(x) for x in zk_servers]
        zk_ip_list = cfgm_ip_list + zoo_ip_list

        #TODO -REMOVE
        cfgm_ip_list_control=[]
        zoo_ip_list_control=[]
        zk_ip_list_control=[]
        for itr in cfgm_ip_list:
            cfgm_ip_list_control.append(self.get_control_ip(provision_params,itr))
        for itr in zoo_ip_list:
            zoo_ip_list_control.append(self.get_control_ip(provision_params,itr))
        for itr in zk_ip_list:
            zk_ip_list_control.append(self.get_control_ip(provision_params,itr))


        contrail_zk_index = len(config_servers) + zk_servers.index(
                provision_params["server_ip"])+1

        # Build Params items
        if self._params_dict.get(
            'zk_ip_list', None) is None:
            self._params_dict['zk_ip_list'] = (
                "[%s]" %(','.join(zk_ip_list)))
        if self._params_dict.get(
            'zk_index', None) is None:
            self._params_dict['zk_index'] = (
                "\"%s\"" %(contrail_zk_index))
        # Build resource items
        data = '''    # Execute Script for all roles.
        contrail_%s::contrail_common::contrail-cfg-zk{contrail_cfg_zk:
            require => %s
        }\n\n''' % (provision_params['puppet_manifest_version'],
                    last_res_added)
        return data


    def puppet_add_config_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''

        if provision_params['server_ip'] in provision_params['roles']['compute']:
            compute_server = provision_params['server_ip']
        else:
            compute_server = provision_params['roles']['compute'][0]
        compute_server_control= self.get_control_ip(provision_params,compute_server)

        control_server_id_lst = ['"%s"' %(x) for x in \
            provision_params['role_ids']['control']]

        config_servers = provision_params['roles']['config']
        if 'zookeeper' in provision_params['roles']:
            zk_servers = provision_params['roles']['zookeeper']
        else:
            zk_servers = []
            cfgm_ip_list = ["\"%s\""%(x) for x in config_servers]
            zoo_ip_list = ["\"%s\""%(x) for x in zk_servers]
            zk_ip_list = cfgm_ip_list + zoo_ip_list
            cfgm_ip_list_control=[]
            zoo_ip_list_control=[]
            zk_ip_list_control=[]
            for itr in cfgm_ip_list:
                cfgm_ip_list_control.append(self.get_control_ip(provision_params,itr))
            for itr in zoo_ip_list:
                zoo_ip_list_control.append(self.get_control_ip(provision_params,itr))
            for itr in zk_ip_list:
                zk_ip_list_control.append(self.get_control_ip(provision_params,itr))

            contrail_cfgm_index = config_servers.index(
                provision_params["server_ip"])+1
            cassandra_ip_list = ["\"%s\""%(x) for x in \
                provision_params['roles']['database']]
        cassandra_ip_list_control=[]
        for itr in cassandra_ip_list:
            cassandra_ip_list_control.append(self.get_control_ip(provision_params,itr))

        openstack_server = provision_params['roles']['openstack'][0]
        openstack_server_control = self.get_control_ip(provision_params,openstack_server)
        
        control_ip_list = ["\"%s\""%(x) for x in \
            provision_params['roles']['control']]

        control_ip_list_control=[]
        for itr in control_ip_list:
            control_ip_list_control.append(self.get_control_ip(provision_params,itr))
            
        if (provision_params['openstack_mgmt_ip'] == ''):
            contrail_openstack_mgmt_ip = provision_params['roles']['openstack'][0]
        else:
            contrail_openstack_mgmt_ip = provision_params['openstack_mgmt_ip']

        contrail_openstack_mgmt_ip_control=self.get_control_ip(provision_params,
                                                    contrail_openstack_mgmt_ip)

        collector_servers = provision_params['roles']['collector']
        if (provision_params["server_ip"] in collector_servers):
            collector_server = provision_params['server_ip']
        else:
            hindex = config_servers.index(provision_params['server_ip'])
            hindex = hindex % len(collector_servers)
            collector_server = collector_servers[hindex]
        collector_server_control = self.get_control_ip(provision_params,collector_server)
        nworkers = 1
        sctl_lines = ''
        for worker_id in range(int(nworkers)):
            sctl_line = 'supervisorctl -s unix:///tmp/supervisord_config.sock ' + \
                        '${1} `basename ${0}:%s`' %(worker_id)
            sctl_lines = sctl_lines + sctl_line
  
        config_server = provision_params['roles']['config'][0]
        config_server_control=self.get_control_ip(provision_params,config_server) 
        config_servers_names = provision_params['role_ids']['config']
        # Keeping openstack index hardcoded untill ha is implemented 
        openstack_index="1"
        rabbit_user_list=[]
        for cfgm_name in config_servers_names:
            rabbit_user_list.append('rabbit@'+str(cfgm_name))
        rabbit_user_list=str(rabbit_user_list)
        rabbit_user_list=rabbit_user_list.replace(" ","")
        #Chhnadak
        amqp_server = provision_params['roles']['config'][0]
        amqp_server_control=self.get_control_ip(provision_params,amqp_server)
        #End here

        # Build Params items
        if self._params_dict.get(
            'contrail_openstack_ip', None) is None:
            self._params_dict['contrail_openstack_ip'] = (
                "\"%s\"" %(openstack_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_mgmt_ip', None) is None:
            self._params_dict['contrail_openstack_mgmt_ip'] = (
                "\"%s\"" %(contrail_openstack_mgmt_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_compute_ip', None) is None:
            self._params_dict['contrail_compute_ip'] = (
                "\"%s\"" %(compute_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_use_certs', None) is None:
            self._params_dict['contrail_use_certs'] = (
                "\"%s\"" %(provision_params["use_certificates"]))
        if self._params_dict.get(
            'contrail_multi_tenancy', None) is None:
            self._params_dict['contrail_multi_tenancy'] = (
                "\"%s\"" %(provision_params["multi_tenancy"]))
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(self.get_control_ip(
                    provision_params,
                    provision_params["server_ip"])))
        if self._params_dict.get(
            'contrail_control_ip_list', None) is None:
            self._params_dict['contrail_control_ip_list'] = (
                "[%s]" %(','.join(control_ip_list_control)))
        if self._params_dict.get(
            'contrail_control_name_list', None) is None:
            self._params_dict['contrail_control_name_list'] = (
                "[%s]" %(','.join(control_server_id_lst)))
        if self._params_dict.get(
            'contrail_collector_ip', None) is None:
            self._params_dict['contrail_collector_ip'] = (
                "\"%s\"" %(collector_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_service_token', None) is None:
            self._params_dict['contrail_service_token'] = (
                "\"%s\"" %(provision_params["service_token"]))
        if self._params_dict.get(
            'contrail_ks_admin_user', None) is None:
            self._params_dict['contrail_ks_admin_user'] = (
                "\"%s\"" %(provision_params["keystone_username"]))
        if self._params_dict.get(
            'contrail_ks_admin_passwd', None) is None:
            self._params_dict['contrail_ks_admin_passwd'] = (
                "\"%s\"" %(provision_params["keystone_password"]))
        if self._params_dict.get(
            'contrail_ks_admin_tenant', None) is None:
            self._params_dict['contrail_ks_admin_tenant'] = (
                "\"%s\"" %(provision_params["keystone_tenant"]))
        if self._params_dict.get(
            'contrail_openstack_root_passwd', None) is None:
            self._params_dict['contrail_openstack_root_passwd'] = (
                "\"%s\"" %(provision_params["openstack_passwd"]))
        if self._params_dict.get(
            'contrail_cassandra_ip_list', None) is None:
            self._params_dict['contrail_cassandra_ip_list'] = (
                "[%s]" %(','.join(cassandra_ip_list_control)))
        if self._params_dict.get(
            'contrail_cassandra_ip_port', None) is None:
            self._params_dict['contrail_cassandra_ip_port'] = (
                "\"9160\"")
        if self._params_dict.get(
            'contrail_zookeeper_ip_list', None) is None:
            self._params_dict['contrail_zookeeper_ip_list'] = (
                "[%s]" %(','.join(cassandra_ip_list_control)))
        if self._params_dict.get(
            'contrail_zk_ip_port', None) is None:
            self._params_dict['contrail_zk_ip_port'] = (
                "\"2181\"" )
        if self._params_dict.get(
            'contrail_redis_ip', None) is None:
            self._params_dict['contrail_redis_ip'] = (
                "\"%s\"" %(self.get_control_ip(
                    provision_params,
                    config_servers[0])))
        if self._params_dict.get(
            'contrail_cfgm_index', None) is None:
            self._params_dict['contrail_cfgm_index'] = (
                "\"%s\"" %(contrail_cfgm_index))
        if self._params_dict.get(
            'contrail_api_nworkers', None) is None:
            self._params_dict['contrail_api_nworkers'] = (
                "\"%s\"" %(nworkers))
        if self._params_dict.get(
            'contrail_supervisorctl_lines', None) is None:
            self._params_dict['contrail_supervisorctl_lines'] = (
                "'%s'" %(sctl_lines))
        if self._params_dict.get(
            'contrail_haproxy', None) is None:
            self._params_dict['contrail_haproxy'] = (
                "\"enable\"" )
        if self._params_dict.get(
            'contrail_uuid', None) is None:
            self._params_dict['contrail_uuid'] = (
                "\"%s\"" %(provision_params['uuid']))
        if self._params_dict.get(
            'contrail_rmq_master', None) is None:
            self._params_dict['contrail_rmq_master'] = (
                "\"%s\"" %(provision_params['rmq_master']))
        if self._params_dict.get(
            'contrail_rmq_is_master', None) is None:
            self._params_dict['contrail_rmq_is_master'] = (
                "\"%s\"" %(provision_params['is_rmq_master']))
        if self._params_dict.get(
            'contrail_region_name', None) is None:
            self._params_dict['contrail_region_name'] = (
                "\"%s\"" %(provision_params['region_name']))
        if self._params_dict.get(
            'contrail_router_asn', None) is None:
            self._params_dict['contrail_router_asn'] = (
                "\"%s\"" %(provision_params['router_asn']))
        if self._params_dict.get(
            'contrail_encap_priority', None) is None:
            self._params_dict['contrail_encap_priority'] = (
                "\"%s\"" %(provision_params['encapsulation_priority']))
        if self._params_dict.get(
            'contrail_bgp_params', None) is None:
            self._params_dict['contrail_bgp_params'] = (
                "\"%s\"" %(provision_params['external_bgp']))
        if self._params_dict.get(
            'contrail_amqp_server_ip', None) is None:
            self._params_dict['contrail_amqp_server_ip'] = (
                "\"%s\"" %(amqp_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_index', None) is None:
            self._params_dict['contrail_openstack_index'] = (
                "\"%s\"" %(openstack_index.replace('"', '')))
        if self._params_dict.get(
            'contrail_rabbit_user', None) is None:
            self._params_dict['contrail_rabbit_user'] = (
                "\"%s\"" %(rabbit_user_list.replace('"', '')))
        if self._params_dict.get(
            'contrail_cfgm_number', None) is None:
            self._params_dict['contrail_cfgm_number'] = (
                "\"%s\"" %(len(config_servers_names)))
        #-END HERE

       
        # Build resource items
        data += '''    # contrail-config role.
    contrail_%s::contrail_config::contrail_config{contrail_config:
        require => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
        last_res_added)
        #add Ha Proxy
        self.create_config_ha_proxy(provision_params)

        return data
        # end puppet_add_config_role

    def puppet_add_control_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
        if provision_params['server_ip'] in provision_params['roles']['config']:
            config_server = provision_params['server_ip']
        else:
            config_server = provision_params['roles']['config'][0]
        config_server_control=self.get_control_ip(provision_params,config_server)

        collector_servers = provision_params['roles']['collector']
        control_servers = provision_params['roles']['control']
        if (provision_params["server_ip"] in collector_servers):
           collector_server = provision_params['server_ip']
        else:
            hindex = control_servers.index(provision_params['server_ip'])
            hindex = hindex % len(collector_servers)
            collector_server = collector_servers[hindex]
        collector_server_control=self.get_control_ip(provision_params, collector_server)
        server_ip_control= self.get_control_ip(provision_params, provision_params["server_ip"])
        nworkers = 1
        # Build Params items
        if self._params_dict.get(
            'contrail_control_ip', None) is None:
            self._params_dict['contrail_control_ip'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_config_port', None) is None:
            self._params_dict['contrail_config_port'] = (
                "\"8443\"")
        if self._params_dict.get(
            'contrail_config_user', None) is None:
            self._params_dict['contrail_config_user'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_config_passwd', None) is None:
            self._params_dict['contrail_config_passwd'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_collector_ip', None) is None:
            self._params_dict['contrail_collector_ip'] = (
                "\"%s\"" %(collector_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_collector_port', None) is None:
            self._params_dict['contrail_collector_port'] = (
                "\"8086\"")
        if self._params_dict.get(
            'contrail_discovery_ip', None) is None:
            self._params_dict['contrail_discovery_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'hostname', None) is None:
            self._params_dict['hostname'] = (
                "\"%s\"" %(provision_params["server_id"]))
        if self._params_dict.get(
            'host_ip', None) is None:
            self._params_dict['host_ip'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'bgp_port', None) is None:
            self._params_dict['bgp_port'] = (
                "\"179\"")
        if self._params_dict.get(
            'cert_ops', None) is None:
            self._params_dict['cert_ops'] = (
                "\"false\"")
        if self._params_dict.get(
            'log_file', None) is None:
            self._params_dict['log_file'] = (
                "\"\"" %())
        if self._params_dict.get(
            'contrail_log_file', None) is None:
            self._params_dict['contrail_log_file'] = (
                "\"--log-file=/var/log/contrail/control.log\"")
        if self._params_dict.get(
            'contrail_api_nworkers', None) is None:
            self._params_dict['contrail_api_nworkers'] = (
                "\"%s\"" %(nworkers))
        # Build resource items
        data += '''    # contrail-control role.
        contrail_%s::contrail_control::contrail_control{contrail_control:
        require => %s
    }\n\n''' % (
        provision_params['puppet_manifest_version'],
        last_res_added)

        return data
    # end puppet_add_control_role

    def puppet_add_collector_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
        config_server = provision_params['roles']['config'][0]
        config_server_control = self.get_control_ip(provision_params, config_server)
        cassandra_ip_list = ["\"%s\""%(x) for x in \
            provision_params['roles']['database']]
        cassandra_ip_list_control=[]
        for itr in cassandra_ip_list:
            cassandra_ip_list_control.append(self.get_control_ip(provision_params, itr))
        collector_servers = provision_params['roles']['collector']
        redis_master_ip = collector_servers[0]
        if (redis_master_ip == provision_params["server_ip"]):
            redis_role = "master"
        else:
            redis_role = "slave"
        redis_master_ip_control = self.get_control_ip(provision_params, redis_master_ip)
        server_ip_control = self.get_control_ip(provision_params, provision_params["server_ip"])
        if server_ip_control in cassandra_ip_list_control:
            cassandra_ip_list_control.remove(server_ip_control)
            cassandra_ip_list_control.insert(0, server_ip_control)
        # Build Params items
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_collector_ip', None) is None:
            self._params_dict['contrail_collector_ip'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_redis_master_ip', None) is None:
            self._params_dict['contrail_redis_master_ip'] = (
                "\"%s\"" %(redis_master_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_redis_role', None) is None:
            self._params_dict['contrail_redis_role'] = (
                "\"%s\"" %(redis_role))
        if self._params_dict.get(
            'contrail_cassandra_ip_list', None) is None:
            self._params_dict['contrail_cassandra_ip_list'] = (
                "[%s]" %(','.join(cassandra_ip_list_control)))
        if self._params_dict.get(
            'contrail_cassandra_ip_port', None) is None:
            self._params_dict['contrail_cassandra_ip_port'] = (
                "\"9160\"")
        if self._params_dict.get(
            'contrail_num_collector_nodes', None) is None:
            self._params_dict['contrail_num_collector_nodes'] = (
                "\"%s\"" %(len(collector_servers)))
        if self._params_dict.get(
            'contrail_analytics_data_ttl', None) is None:
            self._params_dict['contrail_analytics_data_ttl'] = (
                "\"%s\"" %(provision_params["analytics_data_ttl"]))
        # Build resource items
        data += '''    # contrail-collector role.
        contrail_%s::contrail_collector::contrail_collector{contrail_collector:
        require => %s
    }\n\n''' % (provision_params['puppet_manifest_version'],
                last_res_added)
        return data
    # end puppet_add_collector_role

    def puppet_add_webui_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
        if provision_params['server_ip'] in provision_params['roles']['config']:
            config_server = provision_params['server_ip']
        else:
            config_server = provision_params['roles']['config'][0]
        config_server_control=self.get_control_ip(provision_params, config_server)

        webui_ips = provision_params['roles']['webui']
        #TODO Webui_ips_control is not needed
        webui_ips_control=[]
        for itr in webui_ips:
            webui_ips_control.append(self.get_control_ip(provision_params, itr))
        collector_servers = provision_params['roles']['collector']
        collector_servers_control=[]
        for itr in collector_servers:
            collector_servers_control.append(self.get_control_ip(provision_params, itr))
        if (provision_params["server_ip"] in collector_servers):
           collector_server = provision_params['server_ip']
        else:
            hindex = webui_ips.index(provision_params["server_ip"])
            hindex = hindex % len(collector_servers)
            collector_server = collector_servers[hindex]
        collector_server_control = self.get_control_ip(provision_params, collector_server)
        openstack_server = provision_params['roles']['openstack'][0]
        openstack_server_control = self.get_control_ip(provision_params, openstack_server)
        cassandra_ip_list = ["\"%s\""%(x) for x in \
            provision_params['roles']['database']]
        cassandra_ip_list_control=[]
        for itr in cassandra_ip_list:
            cassandra_ip_list_control.append(self.get_control_ip(provision_params, itr))
        # Build Params items
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_collector_ip', None) is None:
            self._params_dict['contrail_collector_ip'] = (
                "\"%s\"" %(collector_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_ip', None) is None:
            self._params_dict['contrail_openstack_ip'] = (
                "\"%s\"" %(openstack_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_cassandra_ip_list', None) is None:
            self._params_dict['contrail_cassandra_ip_list'] = (
                "[%s]" %(','.join(cassandra_ip_list_control)))
        # Build resource items
        data += '''    # contrail-webui role.
        contrail_%s::contrail_webui::contrail_webui{contrail_webui:
        require => %s
    }\n\n''' % (
        provision_params['puppet_manifest_version'],
        last_res_added)
        return data
    # end puppet_add_webui_role

    #Function to create haproxy cfg file for compute nodes
    def create_compute_ha_proxy(self, provision_params):
        
        smgr_dir  = staging_dir = "/etc/puppet/environments/contrail_" + \
            provision_params['puppet_manifest_version'] + "/modules/contrail_" + \
            provision_params['puppet_manifest_version'] + "/files/"

        compute_haproxy_template = string.Template("""
#contrail-compute-marker-start
listen contrail-compute-stats :5938
   mode http
   stats enable
   stats uri /
   stats auth $__contrail_hap_user__:$__contrail_hap_passwd__

$__contrail_disc_stanza__

$__contrail_quantum_stanza__

$__contrail_qpid_stanza__

$__contrail_glance_api_stanza__

#contrail-compute-marker-end
""")


        ds_stanza_template = string.Template("""
$__contrail_disc_frontend__

backend discovery-server-backend
    balance     roundrobin
$__contrail_disc_servers__
    #server  10.84.14.2 10.84.14.2:5998 check
""")

        q_stanza_template = string.Template("""
$__contrail_quantum_frontend__

backend quantum-server-backend
    balance     roundrobin
$__contrail_quantum_servers__
    #server  10.84.14.2 10.84.14.2:9696 check
""")

        g_api_stanza_template = string.Template("""
$__contrail_glance_api_frontend__

backend glance-api-backend
    balance     roundrobin
$__contrail_glance_apis__
    #server  10.84.14.2 10.84.14.2:9292 check
""")

        ds_frontend = textwrap.dedent("""\
        frontend discovery-server 127.0.0.1:5998
            default_backend discovery-server-backend
        """)

        q_frontend = textwrap.dedent("""\
        frontend quantum-server 127.0.0.1:9696
            default_backend quantum-server-backend
        """)

        g_api_frontend = textwrap.dedent("""\
        frontend glance-api 127.0.0.1:9292
            default_backend glance-api-backend
        """)

        haproxy_config = ''

        # if this compute is also config, skip quantum and discovery
        # stanza as they would have been generated in config context
        ds_stanza = ''
        q_stanza = ''

        config_ip_list = provision_params['roles']['config']
        openstack_ip_list = provision_params['roles']['openstack']
        compute_ip = provision_params['server_ip']

        if compute_ip not in config_ip_list:
            # generate discovery service stanza
            ds_server_lines = ''
            for config_ip in config_ip_list:
                host_ip = config_ip

                ds_server_lines = ds_server_lines + \
                '    server %s %s:5998 check\n' %(host_ip, host_ip)

                ds_stanza = ds_stanza_template.safe_substitute({
                    '__contrail_disc_frontend__': ds_frontend,
                    '__contrail_disc_servers__': ds_server_lines,
                    })

            # generate  quantum stanza
            q_server_lines = ''
            for config_ip in config_ip_list:
                host_ip = config_ip

                q_server_lines = q_server_lines + \
                '    server %s %s:9696 check\n' %(host_ip, host_ip)

                q_stanza = q_stanza_template.safe_substitute({
                    '__contrail_quantum_frontend__': q_frontend,
                    '__contrail_quantum_servers__': q_server_lines,
                    })

        # if this compute is also openstack, skip glance-api stanza
        # as that would have been generated in openstack context
        g_api_stanza = ''
        if compute_ip not in openstack_ip_list:
            # generate a glance-api stanza
            g_api_server_lines = ''
            for openstack_ip in openstack_ip_list:
                host_ip = openstack_ip

                g_api_server_lines = g_api_server_lines + \
                '    server %s %s:9292 check\n' %(host_ip, host_ip)

                g_api_stanza = g_api_stanza_template.safe_substitute({
                    '__contrail_glance_api_frontend__': g_api_frontend,
                    '__contrail_glance_apis__': g_api_server_lines,
                    })
                # HACK: for now only one openstack
                break

        compute_haproxy = compute_haproxy_template.safe_substitute({
               '__contrail_hap_user__': 'haproxy',
            '__contrail_hap_passwd__': 'contrail123',
            '__contrail_disc_stanza__': ds_stanza,
            '__contrail_quantum_stanza__': q_stanza,
            '__contrail_glance_api_stanza__': g_api_stanza,
            '__contrail_qpid_stanza__': '',
            })

        ha_proxy_cfg = staging_dir + provision_params['server_id'] + ".cfg"

        shutil.copy2(smgr_dir + "haproxy.cfg", ha_proxy_cfg)
        cfg_file = open(ha_proxy_cfg, 'a')
        cfg_file.write(compute_haproxy)
        cfg_file.close()

    #Function to create haproxy cfg for openstack nodes
    def create_openstack_ha_proxy(self, provision_params):
        smgr_dir  = staging_dir = "/etc/puppet/environments/contrail_" + \
            provision_params['puppet_manifest_version'] + "/modules/contrail_" + \
            provision_params['puppet_manifest_version'] + "/files/"

        openstack_haproxy_template = string.Template("""
#contrail-openstack-marker-start
listen contrail-openstack-stats :5936
   mode http
   stats enable
   stats uri /
   stats auth $__contrail_hap_user__:$__contrail_hap_passwd__

$__contrail_quantum_stanza__

#contrail-openstack-marker-end
""")

        q_stanza_template = string.Template("""
$__contrail_quantum_frontend__

backend quantum-server-backend
    balance     roundrobin
$__contrail_quantum_servers__
    #server  10.84.14.2 10.84.14.2:9696 check
""")

        q_frontend = textwrap.dedent("""\
        frontend quantum-server 127.0.0.1:9696
            default_backend quantum-server-backend
        """)

        config_ip_list = provision_params['roles']['config']
        openstack_ip_list = provision_params['roles']['openstack']
        openstack_ip = provision_params['server_ip']


        # for all openstack, set appropriate haproxy stanzas
        for openstack_ip in openstack_ip_list:
            haproxy_config = ''

            # if this openstack is also config, skip quantum stanza
            # as that would have been generated in config context
            q_stanza = ''
            if openstack_ip not in openstack_ip_list:
                # generate a quantum stanza
                q_server_lines = ''
                for config_ip in config_ip_list:
                    host_ip = config_ip

                    q_server_lines = q_server_lines + \
                    '    server %s %s:9696 check\n' %(host_ip, host_ip)

                    q_stanza = q_stanza_template.safe_substitute({
                        '__contrail_quantum_frontend__': q_frontend,
                        '__contrail_quantum_servers__': q_server_lines,
                        })

            # ...generate new ones
            openstack_haproxy = openstack_haproxy_template.safe_substitute({
                '__contrail_hap_user__': 'haproxy',
                '__contrail_hap_passwd__': 'contrail123',
                '__contrail_quantum_stanza__': q_stanza,
                })

            ha_proxy_cfg = staging_dir + provision_params['server_id'] + ".cfg"

            shutil.copy2(smgr_dir + "haproxy.cfg", ha_proxy_cfg)
            cfg_file = open(ha_proxy_cfg, 'a')
            cfg_file.write(openstack_haproxy)
            cfg_file.close()

    def puppet_add_compute_role(self, provision_params, last_res_added):
        # Get all the parameters needed to send to puppet manifest.
        data = ''
        #Vm on top of ESX Server
        if 'esx_server' in provision_params.keys():
            print "esx_server"
            #call scripts to provision esx
            vm_params = {}
            vm_params['vm'] = "ContrailVM"
            vm_params['vmdk'] = "ContrailVM"
            vm_params['datastore'] = provision_params['datastore']
            vm_params['eth0_mac'] = provision_params['server_mac']
            vm_params['eth0_ip'] = provision_params['server_ip']
            vm_params['eth0_pg'] = provision_params['esx_fab_port_group']
            vm_params['eth0_vswitch'] = provision_params['esx_fab_vswitch']
            vm_params['eth0_vlan'] = None
            vm_params['eth1_vswitch'] = provision_params['esx_vm_vswitch']
            vm_params['eth1_pg'] = provision_params['esx_vm_port_group']
            vm_params['eth1_vlan'] = "4095"
            vm_params['uplink_nic'] = provision_params['esx_uplink_nic']
            vm_params['uplink_vswitch'] = provision_params['esx_fab_vswitch']
            vm_params['server'] = provision_params['esx_ip']
            vm_params['username'] = provision_params['esx_username']
            vm_params['password'] = provision_params['esx_password']
            vm_params['thindisk'] =  provision_params['esx_vmdk']
            vm_params['smgr_ip'] = provision_params['smgr_ip'];
            vm_params['domain'] =  provision_params['domain']
            vm_params['vm_password'] = provision_params['password']
            vm_params['vm_server'] = provision_params['server_id']
            vm_params['vm_deb'] = provision_params['vm_deb']
            out = ContrailVM(vm_params)
            print out

        control_servers = provision_params['roles']['control']
        #control_servers_control = self.get_control_ip(provision_params,control_servers)

        if provision_params['server_ip'] in provision_params['roles']['config']:
            config_server = provision_params['server_ip']
        else:
            config_server = provision_params['roles']['config'][0]
        config_server_control = self.get_control_ip(provision_params,config_server)
      

        if provision_params['server_ip'] in provision_params['roles']['collector']:
            collector_server = provision_params['server_ip']
        else:
            collector_server = provision_params['roles']['collector'][0]
        collector_server_control = self.get_control_ip(provision_params,collector_server)

        if provision_params['server_ip'] in provision_params['roles']['openstack']:
            openstack_server = provision_params['server_ip']
        else:
            openstack_server = provision_params['roles']['openstack'][0]
        openstack_server_control= self.get_control_ip(provision_params,openstack_server)

        contrail_openstack_mgmt_ip = provision_params['roles']['openstack'][0]
        contrail_openstack_mgmt_ip_control= self.get_control_ip(provision_params,contrail_openstack_mgmt_ip)
        server_ip_control= self.get_control_ip(provision_params,provision_params["server_ip"])
        provision_params["compute_non_mgmt_ip"] = provision_params["server_ip"]
        provision_params["compute_non_mgmt_gway"] = provision_params['server_gway']


        if 'contrail_params' in  provision_params:
            contrail_dict = eval(provision_params['contrail_params'])
            control_data_intf = contrail_dict['control_data_interface']
            if provision_params['interface_list'] and \
                     provision_params['interface_list'] [control_data_intf]:
                non_mgmt_ip = provision_params['interface_list'] \
                                [control_data_intf] ['ip']
                non_mgmt_gw =  provision_params['interface_list'] \
                                [control_data_intf] ['d_gw']
        elif provision_params['intf_control']:
            intf_control = eval(provision_params['intf_control'])
            for intf,values in intf_control.items():
                non_mgmt_ip= values['ip_address'].split("/")[0]
                non_mgmt_gw= values['gateway']
        else:
            non_mgmt_ip = provision_params["compute_non_mgmt_ip"]
            non_mgmt_gw = provision_params["compute_non_mgmt_gway"] 
        # Keeping openstack index hardcoded untill ha is implemented 
        openstack_index="1"
        #Chhnadak
        amqp_server = provision_params['roles']['config'][0]
        amqp_server_control=self.get_control_ip(provision_params,amqp_server)
        #End here
 
#	if provision_params['haproxy'] == 'enable':
#            data += '''         #Source HA Proxy CFG
#        contrail-common::haproxy-cfg{haproxy_cfg:
#            server_id => "%s"}\n\n
#''' % (server["server_id"])
        # Build Params items
        if self._params_dict.get(
            'contrail_config_ip', None) is None:
            self._params_dict['contrail_config_ip'] = (
                "\"%s\"" %(config_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_compute_hostname', None) is None:
            self._params_dict['contrail_compute_hostname'] = (
                "\"%s\"" %(provision_params["server_id"]))
        if self._params_dict.get(
            'contrail_compute_ip', None) is None:
            self._params_dict['contrail_compute_ip'] = (
                "\"%s\"" %(server_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_collector_ip', None) is None:
            self._params_dict['contrail_collector_ip'] = (
                "\"%s\"" %(collector_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_ip', None) is None:
            self._params_dict['contrail_openstack_ip'] = (
                "\"%s\"" %(openstack_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_mgmt_ip', None) is None:
            self._params_dict['contrail_openstack_mgmt_ip'] = (
                "\"%s\"" %(contrail_openstack_mgmt_ip_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_service_token', None) is None:
            self._params_dict['contrail_service_token'] = (
                "\"%s\"" %(provision_params["service_token"]))
        if self._params_dict.get(
            'contrail_physical_interface', None) is None:
            self._params_dict['contrail_physical_interface'] = (
                "\"%s\"" %(provision_params["phy_interface"]))
        # Restrict the numbe of control nodes to two for agent
        contrail_num_controls = 2
        if self._params_dict.get(
            'contrail_num_controls', None) is None:
            self._params_dict['contrail_num_controls'] = (
                "\"%s\"" %(contrail_num_controls))
        if self._params_dict.get(
            'contrail_non_mgmt_ip', None) is None:
            self._params_dict['contrail_non_mgmt_ip'] = (
                "\"%s\"" %(non_mgmt_ip))
        if self._params_dict.get(
            'contrail_non_mgmt_gw', None) is None:
            self._params_dict['contrail_non_mgmt_gw'] = (
                "\"%s\"" %(non_mgmt_gw))
        if self._params_dict.get(
            'contrail_ks_admin_user', None) is None:
            self._params_dict['contrail_ks_admin_user'] = (
                "\"%s\"" %(provision_params["keystone_username"]))
        if self._params_dict.get(
            'contrail_ks_admin_passwd', None) is None:
            self._params_dict['contrail_ks_admin_passwd'] = (
                "\"%s\"" %(provision_params["keystone_password"]))
        if self._params_dict.get(
            'contrail_ks_admin_tenant', None) is None:
            self._params_dict['contrail_ks_admin_tenant'] = (
                "\"%s\"" %(provision_params["keystone_tenant"]))
        if self._params_dict.get(
            'contrail_haproxy', None) is None:
            self._params_dict['contrail_haproxy'] = (
                "\"%s\"" %(provision_params["haproxy"]))
        if self._params_dict.get(
            'contrail_vm_ip', None) is None:
            self._params_dict['contrail_vm_ip'] = (
                "\"%s\"" %(provision_params["esx_ip"]))
        if self._params_dict.get(
            'contrail_vm_username', None) is None:
            self._params_dict['contrail_vm_username'] = (
                "\"%s\"" %(provision_params["esx_username"]))
        if self._params_dict.get(
            'contrail_vm_passwd', None) is None:
            self._params_dict['contrail_vm_passwd'] = (
                "\"%s\"" %(provision_params["esx_password"]))
        if self._params_dict.get(
            'contrail_vswitch', None) is None:
            self._params_dict['contrail_vswitch'] = (
                "\"%s\"" %(provision_params["esx_vm_vswitch"]))
        if self._params_dict.get(
            'contrail_amqp_server_ip', None) is None:
            self._params_dict['contrail_amqp_server_ip'] = (
                "\"%s\"" %(amqp_server_control.replace('"', '')))
        if self._params_dict.get(
            'contrail_openstack_index', None) is None:
            self._params_dict['contrail_openstack_index'] = (
                "\"%s\"" %(openstack_index.replace('"', '')))
        # Build resource items
        data += '''    # contrail-compute role.
    contrail_%s::contrail_compute::contrail_compute{contrail_compute:
        require => %s
    }\n\n''' % (
        provision_params['puppet_manifest_version'],
        last_res_added)


        if provision_params["haproxy"] == "enable":
            self.create_compute_ha_proxy(provision_params)

        return data
    # end puppet_add_compute_role


    def puppet_add_storage_role(self, provision_params, last_res_added):
        data = ''
        req = '''Contrail_%s::Contrail_storage::Contrail_storage[\"contrail_storage\"]''' \
              % (provision_params['puppet_manifest_version'])
        if (provision_params['openstack_mgmt_ip'] == ''):
            contrail_openstack_mgmt_ip = provision_params["server_ip"]
        else:
            contrail_openstack_mgmt_ip = provision_params['openstack_mgmt_ip']
        if provision_params['server_ip'] in provision_params['roles']['storage-compute']:
            data += '''    # contrail-storage role.
    contrail_%s::contrail_storage::contrail_storage{contrail_storage:
        contrail_storage_repo_id => "%s",
        contrail_num_storage_hosts => %s,
        contrail_storage_fsid => "%s",
        contrail_storage_virsh_uuid => "%s",
        contrail_openstack_ip => "$contrail_openstack_ip",
        contrail_storage_mon_secret => "%s",
        contrail_storage_admin_key => "%s",
        contrail_storage_osd_bootstrap_key => "%s",
        contrail_storage_mon_hosts => \"''' % (
                provision_params['puppet_manifest_version'],
                provision_params['storage_repo_id'],
                provision_params['num_storage_hosts'],
                provision_params['storage_fsid'],
                provision_params['storage_virsh_uuid'],
                provision_params['storage_mon_secret'],
                provision_params['admin_key'],
                provision_params['osd_bootstrap_key'])
            for key in provision_params['storage_monitor_hosts']:
                data += '''%s ,''' % key
            data = data[:len(data)-1]+'''\",'''
            if 'storage_server_disks' in provision_params:
                data += '''
        contrail_storage_osd_disks => ['''
                for key in provision_params['storage_server_disks']:
                    data += '''\'%s\' ,''' % key
                data = data[:len(data) - 1] + '''],'''
            else:
                pass
            data += '''
        require => %s
    }\n''' % last_res_added
        return data

    def puppet_add_storage_manager_role(self, provision_params, last_res_added):
        data = ''
        if (provision_params['openstack_mgmt_ip'] == ''):
            contrail_openstack_mgmt_ip = provision_params["server_ip"]
        else:
            contrail_openstack_mgmt_ip = provision_params['openstack_mgmt_ip']
        if provision_params['server_ip'] not in set(provision_params['roles']['storage-compute']):
            data += '''    # contrail-storage-manager role.
    contrail_%s::contrail_storage::contrail_storage{contrail_storage:
        contrail_storage_repo_id => "%s",
        contrail_num_storage_hosts => %s,
        contrail_storage_fsid => "%s",
        contrail_storage_virsh_uuid => "%s",
        contrail_openstack_ip => "$contrail_openstack_ip",
        contrail_storage_mon_secret => "%s",
        contrail_storage_admin_key => "%s",
        contrail_storage_osd_bootstrap_key => "%s",
        contrail_storage_mon_hosts => \"''' % (
                provision_params['puppet_manifest_version'],
                provision_params['storage_repo_id'],
                provision_params['num_storage_hosts'],
                provision_params['storage_fsid'],
                provision_params['storage_virsh_uuid'],
                provision_params['storage_mon_secret'],
                provision_params['admin_key'],
                provision_params['osd_bootstrap_key'])
            for key in provision_params['storage_monitor_hosts']:
                data += '''%s ,''' % key
            data = data[:len(data) - 1] + '''\",'''
            data += '''
        require => %s
    }
    ''' % last_res_added

            data += '''\n\n'''

        return data

    #end puppet_add_storage_role

    _roles_function_map = {
        "common": puppet_add_common_role,
        "database": puppet_add_database_role,
        "openstack": puppet_add_openstack_role,
        "config": puppet_add_config_role,
        "control": puppet_add_control_role,
        "collector": puppet_add_collector_role,
        "webui": puppet_add_webui_role,
        "zookeeper": puppet_add_zk_role,
        "compute": puppet_add_compute_role,
        "storage-compute": puppet_add_storage_role,
        "storage-master": puppet_add_storage_manager_role
    }

    def delete_node_entry(self, site_file, server_fqdn):
        tempfd, temp_file = tempfile.mkstemp()
        fh = os.fdopen(tempfd, "w")
        node_found = False
        brace_count = 0
        with open(site_file, "r") as site_fh:
            for line in site_fh:
                tokens = line.strip().split()
                if ((len(tokens) >= 2) and
                    (tokens[0] == "node") and
                    ((re.findall(r"['\"](.*?)['\"]", tokens[1]))[0] == server_fqdn)):
                    node_found = True
                #end if tokens...
                if not node_found:
                    fh.write(line)
                else:
                    # skip comments
                    if tokens[0].startswith("#"):
                        continue
                    # Skip lines till closing brace
                    if "{" in line:
                        brace_count += 1
                    if "}" in line:
                        brace_count -= 1
                    if brace_count == 0:
                        node_found = False
                # end else not node_found
            # end for
        # end with
        fh.close()
        shutil.copy(temp_file, site_file)
        os.remove(temp_file)
    # end def delete_node_entry

    def add_node_entry(
        self, site_file, provision_params,
        server, cluster, cluster_servers):
        cluster_params = eval(cluster['parameters'])
        server_fqdn = provision_params['server_id'] + "." + \
            provision_params['domain']
        data = ''
        data += "node \'%s\' {\n" %server_fqdn
        # Add Stage relationships
        data += '    stage{ \'first\': }\n'
        data += '    stage{ \'last\': }\n'
        data += '    stage{ \'compute\': }\n'
        data += '    stage{ \'pre\': }\n'
        data += '    stage{ \'post\': }\n'
	if 'storage-compute' in server['roles'] or 'storage-master' in server['roles']:
            data += '    stage{ \'storage\': }\n'
        data += '    Stage[\'pre\']->Stage[\'first\']->Stage[\'main\']->Stage[\'last\']->Stage[\'compute\']->'
	if 'storage-compute' in server['roles'] or 'storage-master' in server['roles']:
            data += 'Stage[\'storage\']->'
        data += 'Stage[\'post\']\n'

        # Add pre role
        data += '    class { \'::contrail::provision_start\' : state => \'provision_started\', stage => \'pre\' }\n'
        # Add common role
        data += '    class { \'::contrail::profile::common\' : stage => \'first\' }\n'
        # Add keepalived (This class is no-op if vip is not configured.)
        if 'config' in server['roles']:
            data += '    include ::contrail::profile::keepalived\n'
        # Add haproxy (for config node)
        if 'config' in server['roles']:
            data += '    include ::contrail::profile::haproxy\n'
        # Add database role.
        if 'database' in server['roles']:
            data += '    include ::contrail::profile::database\n'
        # Add webui role.
        if 'webui' in server['roles']:
            data += '    include ::contrail::profile::webui\n'
        # Add openstack role.
        if 'openstack' in server['roles']:
            if cluster_params.get("internal_vip", "") != "" :
                data += '    class { \'::contrail::profile::openstack_controller\': } ->\n'
                if 'config' in server['roles']:
                    data += '    class { \'::contrail::ha_config\': } ->\n'
                else:
                    data += '    class { \'::contrail::ha_config\': }\n'
            else:
                data += '    include ::contrail::profile::openstack_controller\n'
        # Add config role.
        if 'config' in server['roles']:
            if cluster_params.get("internal_vip", "") != "" :
                data += '    class { \'::contrail::profile::config\': }\n'
            else:
                data += '    include ::contrail::profile::config\n'

        # Add controller role.
        if 'control' in server['roles']:
            data += '    include ::contrail::profile::controller\n'
        # Add collector role.
        if 'collector' in server['roles']:
            data += '    include ::contrail::profile::collector\n'
        # Add config provision role.
        if 'config' in server['roles']:
            data += '    class { \'::contrail::profile::provision\' : stage => \'last\' }\n'
        # Add compute role
        if 'compute' in server['roles']:
            data += '    class { \'::contrail::profile::compute\' : stage => \'compute\' }\n'
        # Add Storage Role
	if 'storage-compute' in server['roles'] or 'storage-master' in server['roles']:
            data += '    class { \'::contrail::profile::storage\' :  stage => \'storage\' }\n'
        # Add post role
        data += '    class { \'::contrail::provision_complete\' : state => \'provision_completed\', stage => \'post\' }\n'

        data += "}\n"
        with open(site_file, "a") as site_fh:
            site_fh.write(data)
        os.chmod(site_file, 0644)
        # end with
    # end def add_node_entry

    def add_cluster_parameters(self, cluster_params):
        cluster_params_mapping = {
            "uuid" : ["uuid", "string"],
            "internal_vip" : ["internal_vip", "string"],
            "external_vip" : ["external_vip", "string"],
            "contrail_internal_vip" : ["contrail_internal_vip", "string"],
            "contrail_external_vip" : ["contrail_external_vip", "string"],
            "analytics_data_ttl" : ["analytics_data_ttl", "integer"],
            "analytics_syslog_port" : ["analytics_syslog_port", "integer"],
            "database_dir" : ["database_dir", "string"],
            "analytics_data_dir" : ["analytics_data_dir", "string"],
            "ssd_data_dir" : ["ssd_data_dir", "string"],
            "keystone_ip" : ["keystone_ip", "string"],
            "keystone_password" : ["keystone_admin_password", "string"],
            "service_token" : ["keystone_service_token", "string"],
            "keystone_username" : ["keystone_admin_user", "string"],
            "keystone_tenant" : ["keystone_admin_tenant", "string"],
            "keystone_service_tenant" : ["keystone_service_tenant", "string"],
            "keystone_region_name" : ["keystone_region_name", "string"],
            "multi_tenancy" : ["multi_tenancy", "boolean"],
            "zookeeper_ip_list" : ["zookeeper_ip_list", "array"],
            "haproxy" : ["haproxy_flag", "string"],
            "hc_interval" : ["hc_interval", "integer"],
            "nfs_server" : ["nfs_server", "string"],
            "nfs_glance_path" : ["nfs_glance_path", "string"],
            "database_token" : ["database_initial_token", "integer"],
            "encapsulation_priority" : ["encap_priority", "string"],
            "router_asn" : ["router_asn", "string"],
            "external_bgp" : ["external_bgp", "string"],
            "use_certificates" : ["use_certs", "boolean"],
            "contrail_logoutput" : ["contrail_logoutput", "boolean"]
        }

        data = ''

        # Go thru all the keys above and if present, add to parameter list
        for k,v in cluster_params_mapping.items():
            if k in cluster_params:
                # if value is text, add with quotes, else without the quotes.
                if v[1].lower() == "string":
                    data += 'contrail::params::' + v[0] + ': "' + \
                        cluster_params.get(k, "") + '"\n'
                else:
                    data += 'contrail::params::' + v[0] + ': ' + \
                        cluster_params.get(k, "") + '\n'
                # end if-else
        # end for
        return data
    # end add cluster_parameters

    def initiate_esx_contrail_vm(self, provision_params):
        if 'esx_server' in provision_params.keys():
            self._smgr_log.log(self._smgr_log.DEBUG, "esx_server")
            #call scripts to provision esx
            vm_params = {}
            vm_params['vm'] = "ContrailVM"
            vm_params['vmdk'] = "ContrailVM"
            vm_params['datastore'] = provision_params['datastore']
            vm_params['eth0_mac'] = provision_params['server_mac']
            vm_params['eth0_ip'] = provision_params['server_ip']
            vm_params['eth0_pg'] = provision_params['esx_fab_port_group']
            vm_params['eth0_vswitch'] = provision_params['esx_fab_vswitch']
            vm_params['eth0_vlan'] = None
            vm_params['eth1_vswitch'] = provision_params['esx_vm_vswitch']
            vm_params['eth1_pg'] = provision_params['esx_vm_port_group']
            vm_params['eth1_vlan'] = "4095"
            vm_params['uplink_nic'] = provision_params['esx_uplink_nic']
            vm_params['uplink_vswitch'] = provision_params['esx_fab_vswitch']
            vm_params['server'] = provision_params['esx_ip']
            vm_params['username'] = provision_params['esx_username']
            vm_params['password'] = provision_params['esx_password']
            vm_params['thindisk'] =  provision_params['esx_vmdk']
            vm_params['smgr_ip'] = provision_params['smgr_ip'];
            vm_params['domain'] =  provision_params['domain']
            vm_params['vm_password'] = provision_params['password']
            vm_params['vm_server'] = provision_params['server_id']
            vm_params['vm_deb'] = provision_params['vm_deb']
            out = ContrailVM(vm_params)
            self._smgr_log.log(self._smgr_log.DEBUG, "ContrilVM:" %(out))
    # end initiate_esx_contrail_vm

    def build_contrail_hiera_file(
        self, hiera_filename, provision_params,
        server, cluster, cluster_servers):
        cluster_params = eval(cluster['parameters'])
        server_params = eval(server['parameters'])
        data = ''
        package_ids = [provision_params.get('package_image_id', "").encode('ascii')]
        package_types = [provision_params.get('package_type', "").encode('ascii')]
        if 'esx_server' in provision_params and 'compute' in provision_params['host_roles']:
            self.initiate_esx_contrail_vm(provision_params)
	if 'storage-compute' in provision_params['host_roles'] or 'storage-master' in provision_params['host_roles']:
            package_ids.append(provision_params.get('storage_repo_id', "").encode('ascii'))
            package_types.append("contrail-ubuntu-storage-repo".encode('ascii'))
        data += 'contrail::params::contrail_repo_name: %s\n' %(str(package_ids))
        data += 'contrail::params::contrail_repo_type: %s\n' %(str(package_types))

        data += 'contrail::params::host_ip: "%s"\n' %(
            self.get_control_ip(provision_params, server.get('ip_address', "")))

        #Upgrade Kernel
        if 'kernel_upgrade' in provision_params and \
            provision_params['kernel_upgrade'] != DEFAULT_KERNEL_UPGRADE :
            data += 'contrail::params::kernel_upgrade: "%s"\n' %(
                provision_params.get('kernel_upgrade', DEFAULT_KERNEL_UPGRADE))
        if 'kernel_version' in provision_params and \
            provision_params['kernel_version'] != DEFAULT_KERNEL_VERSION :
            data += 'contrail::params::kernel_version: "%s"\n' %(
                provision_params.get('kernel_version', DEFAULT_KERNEL_VERSION))
        if 'external_bgp' in provision_params and \
            provision_params['external_bgp'] :
            data += 'contrail::params::external_bgp: "%s"\n' %(
                provision_params.get('external_bgp', ""))
        if "uuid" in cluster_params:
            data += 'contrail::params::uuid: "%s"\n' %(
                cluster_params.get('uuid', ""))

        role_ips = {}
        role_ids = {}
        role_passwd = {}
        role_users = {}
        for role in ['database', 'config', 'openstack',
                     'control', 'collector',
                     'webui', 'compute']:
            role_ips[role] = [
                self.get_control_ip(provision_params, x["ip_address"].encode('ascii')) \
                    for x in cluster_servers if role in set(eval(x['roles']))]
            data += 'contrail::params::%s_ip_list: %s\n' %(
                role, str(role_ips[role]))
            role_ids[role] = [
                x["id"].encode('ascii') for x in cluster_servers if role in set(eval(x['roles']))]
            data += 'contrail::params::%s_name_list: %s\n' %(
                role, str(role_ids[role]))
            role_passwd[role] = [
                x["password"].encode('ascii') for x in cluster_servers if role in set(eval(x['roles']))]
            data += 'contrail::params::%s_passwd_list: %s\n' %(
                role, str(role_passwd[role]))
            role_users[role] = [
                "root".encode('ascii') for x in cluster_servers if role in set(eval(x['roles']))]
            data += 'contrail::params::%s_user_list: %s\n' %(
                role, str(role_users[role]))

        if (server['id'] == role_ids['openstack'][0]) :
           data += 'contrail::params::sync_db: %s\n' %(
               "True")
        else:
           data += 'contrail::params::sync_db: %s\n' %(
               "False")
 

        # Retrieve and add all the cluster parameters specified.
        data += self.add_cluster_parameters(cluster_params)
        # Handle any other additional parameters to be added to yaml file.
        # openstack_mgmt_ip_list
        openstack_mgmt_ip_list = [x["ip_address"].encode('ascii') \
                for x in cluster_servers if "openstack" in set(eval(x['roles']))]
        data += 'contrail::params::openstack_mgmt_ip_list: %s\n' %(
            str(openstack_mgmt_ip_list))
        # host_non_mgmt_ip
        server_mgmt_ip = server.get("ip_address", "").encode('ascii')
        server_control_ip = self.get_control_ip(
            provision_params, server_mgmt_ip)
        if (server_control_ip != server_mgmt_ip):
            data += 'contrail::params::host_non_mgmt_ip: "%s"\n' %(
                server_control_ip)
            # host_non_mgmt_gateway
            control_intf_dict = provision_params.get("control_net", "")
            if control_intf_dict:
                server_control_intf = eval(control_intf_dict.get(server_mgmt_ip, ""))
                if server_control_intf:
                    intf_name, intf_details = server_control_intf.popitem()
                    data += 'contrail::params::host_non_mgmt_gateway: "%s"\n' %(
                        intf_details.get("gateway", ""))
                # end if server_control_intf
            # end if control_intf_dict
        # enf if server_control_ip...


	if 'storage-compute' in provision_params['host_roles'] or 'storage-master' in provision_params['host_roles']:
            ## Storage code
            data += 'contrail::params::host_roles: %s\n' %(str(provision_params['host_roles']))
            data += 'contrail::params::storage_num_osd: %s\n' %(provision_params['storage_num_osd'])
            data += 'contrail::params::storage_fsid: "%s"\n' %(provision_params['storage_fsid'])
            data += 'contrail::params::storage_num_hosts: %s\n' %(provision_params['num_storage_hosts'])
            data += 'contrail::params::storage_virsh_uuid: "%s"\n' %(provision_params['storage_virsh_uuid'])
            data += 'contrail::params::storage_monitor_secret: "%s"\n' %(provision_params['storage_mon_secret'])
            data += 'contrail::params::storage_admin_key: "%s"\n' %(provision_params['admin_key'])
            data += 'contrail::params::osd_bootstrap_key: "%s"\n' %(provision_params['osd_bootstrap_key'])
            data += 'contrail::params::storage_enabled: "%s"\n' %(provision_params['contrail-storage-enabled'])
            data += 'contrail::params::live_migration_storage_scope: "%s"\n' %(provision_params['live_migration_storage_scope'])
            data += 'contrail::params::live_migration_host: "%s"\n' %(provision_params['live_migration_host'])
            storage_mon_hosts = ''
            for key in provision_params['storage_monitor_hosts']:
                storage_mon_hosts += '''%s, ''' % key
            data += 'contrail::params::storage_monitor_hosts: "%s"\n' %(str(storage_mon_hosts))

            storage_hostnames = ''
            for key in provision_params['storage_hostnames']:
                storage_hostnames += '''"%s", ''' % key
            data += 'contrail::params::storage_hostnames: \'[%s]\'\n' %(str(storage_hostnames))

            if 'storage-master' in provision_params['host_roles']:
                storage_chassis_config = ''
                for key in provision_params['storage_chassis_config']:
                    storage_chassis_config += '''"%s", ''' % key
                if len(str(storage_chassis_config)) != 0:
                    data += 'contrail::params::storage_chassis_config: \'[%s]\'\n' %(str(storage_chassis_config))
    
            if 'storage_server_disks' in provision_params:
                storage_disks = [  x.encode('ascii') for x in provision_params['storage_server_disks']]
                data += 'contrail::params::storage_osd_disks: %s\n' %(str(storage_disks))
            else:
                data += 'contrail::params::storage_osd_disks: []\n' 
            control_network = self.storage_get_control_network_mask(provision_params, server, cluster)
            self._smgr_log.log(self._smgr_log.DEBUG, "control-net : %s" %(control_network))
            data += 'contrail::params::storage_cluster_network: %s\n' %(control_network) 

        with open(hiera_filename, "w") as site_fh:
            site_fh.write(data)
        # end with
    # end def build_contrail_hiera_file

    # Use template to prepare hiera data file for openstack modules. Revisit later to refine.
    def build_openstack_hiera_file(
        self, hiera_filename, provision_params,
        server, cluster, cluster_servers):
        mysql_allowed_hosts = []
        role_ips_dict = provision_params['roles']
        cluster_params = eval(cluster['parameters'])
        server_params = eval(server['parameters'])
        # Get all values needed to fill he template.
        self_ip = server.get("ip_address", "")
        openstack_ip = cluster_params.get("internal_vip", None)
        contrail_internal_vip = cluster_params.get("contrail_internal_vip", None)
        contrail_external_vip = cluster_params.get("contrail_external_vip", None)
        external_vip = cluster_params.get("external_vip", None)

        if contrail_internal_vip != None and contrail_internal_vip != "":
            mysql_allowed_hosts.append(contrail_internal_vip)
        if contrail_external_vip != None and contrail_external_vip != "":
            mysql_allowed_hosts.append(contrail_external_vip)
        if external_vip != None and external_vip != "":
            mysql_allowed_hosts.append(external_vip)


        os_ip_list =  [self.get_control_ip(provision_params, x["ip_address"].encode('ascii')) \
                    for x in cluster_servers if 'openstack' in set(eval(x['roles']))]

        config_ip_list =  [self.get_control_ip(provision_params, x["ip_address"].encode('ascii')) \
                    for x in cluster_servers if 'config' in set(eval(x['roles']))]

        if openstack_ip != None and openstack_ip != "":
            mysql_allowed_hosts.append(openstack_ip)
        mysql_allowed_hosts = mysql_allowed_hosts + list(set(os_ip_list + config_ip_list + role_ips_dict['config'] + role_ips_dict['openstack'] ))

        if openstack_ip is None or openstack_ip == '':
            if self_ip in role_ips_dict['openstack']:
                openstack_ip = self_ip
            else:
                openstack_ip = role_ips_dict['openstack'][0]
        
        subnet_mask = server.get("subnet_mask", "")
        if not subnet_mask:
            subnet_mask = cluster_params.get("subnet_mask", "255.255.255.0")
        mysql_root_password = cluster_params.get("mysql_root_password", "c0ntrail123")
        keystone_admin_token = cluster_params.get("service_token", "contrail123")
        keystone_admin_password = cluster_params.get("keystone_password", "contrail123")
        subnet_address = str(IPNetwork(
            openstack_ip + "/" + subnet_mask).network)
        subnet_octets = subnet_address.split(".")
        if subnet_octets[3] == "0":
            subnet_octets[3] = "%"
            if subnet_octets[2] == "0":
                subnet_octets[2] = "%"
                if subnet_octets[1] == "0":
                    subnet_octets[1] = "%"
        #mysql_allowed_hosts = openstack_ip 
        template_vals = {
            '__openstack_ip__': openstack_ip,
            '__subnet_mask__': subnet_mask,
            '__mysql_root_password__': mysql_root_password,
            '__mysql_service_password__': mysql_root_password,
            '__keystone_admin_token__': keystone_admin_token,
            '__keystone_admin_password__': keystone_admin_password,
            '__mysql_allowed_hosts__': (', '.join("'" + item + "'" for item in mysql_allowed_hosts)),
            '__openstack_password__': keystone_admin_password
        }
        data = openstack_hieradata.template.safe_substitute(template_vals)
        outfile = open(hiera_filename, 'w')
        outfile.write(data)
        outfile.close()
    # end def build_openstack_hiera_file

    def build_hiera_files(
        self, hieradata_dir, provision_params,
        server, cluster, cluster_servers):
        server_fqdn = provision_params['server_id'] + "." + \
            provision_params['domain']
        contrail_hiera_file = hieradata_dir + server_fqdn + \
            "-contrail.yaml"
        self.build_contrail_hiera_file(
            contrail_hiera_file, provision_params, server,
            cluster, cluster_servers)
        openstack_hiera_file = hieradata_dir + server_fqdn + \
            "-openstack.yaml"
        self.build_openstack_hiera_file(
            openstack_hiera_file, provision_params, server,
            cluster, cluster_servers)
    # end def build_hieradata_files

    def new_provision_server(
        self, provision_params, server, cluster, cluster_servers):
        server_fqdn = provision_params['server_id'] + "." + \
            provision_params['domain']
        env_name = provision_params['puppet_manifest_version']
        env_name = env_name.replace('-', '_')
        site_file = self.puppet_directory + "environments/" + \
            env_name + "/manifests/site.pp"
        hieradata_dir = self.puppet_directory + "environments/" + \
            env_name + "/hieradata/"
        # Build Hiera data for the server
        self.build_hiera_files(
            hieradata_dir, provision_params,
            server, cluster, cluster_servers)
        # Create an entry for this node in site.pp.
        # First, delete any existing entry and then add a new one.
        self.delete_node_entry(site_file, server_fqdn)
        # Now add a new node entry
        self.add_node_entry(
            site_file, provision_params, server, cluster, cluster_servers)

        # Add entry for the server to environment mapping in 
        # node_mapping.json file.
        self.update_node_map_file(provision_params['server_id'],
                                  provision_params['domain'],
                                  env_name)
    # end def new_provision_server

    # Function to remove puppet files and entries created when provisioning the server. This is called
    # when server is being reimaged. We do not want old provisioning data to be retained.
    def new_unprovision_server(self, server_id, server_domain):
        server_fqdn = server_id + "." + server_domain
        # Remove node to environment mapping from node_mapping.json file.
	node_env_dict = {}
        env_name = self.update_node_map_file(server_id, server_domain, None)
        if env_name is None:
            return
        # Remove server node entry from site.pp.
        site_file = self.puppet_directory + "environments/" + \
            env_name + "/manifests/site.pp"
        try:
            self.delete_node_entry(site_file, server_fqdn)
	except:
	    pass
        # Remove Hiera Data files for the server.
        hiera_datadir = self.puppet_directory + "environments/" + \
            env_name + "/hieradata/"
        try:
            os.remove(hiera_datadir + server_fqdn + "-contrail.yaml")
            os.remove(hiera_datadir + server_fqdn + "-openstack.yaml")
	except:
	    pass
    # end new_unprovision_server()


    # env_name empty string or None is to remove the entry from the map file.
    # env_name value specified will be updated to the map file.
    # env_name could be valid one or invalid manifest.
    #        invalid valid manifest is used to turn off the agent puppet run
    # server_id and domain are required for both update and delete of an entry
    def update_node_map_file(self, server_id, server_domain, env_name):
        if not server_id or not server_domain:
            return None

        server_fqdn = server_id + "." + server_domain
        node_env_map_file = self.smgr_base_dir+self._node_env_map_file
        
        try:
            with open(node_env_map_file, "r") as env_file:
                node_env_dict = json.load(env_file)
            # end with
        except:
            msg = "Not able open environment map file %s" % (node_env_map_file)
            self._smgr_log.log(self._smgr_log.ERROR, msg)
            return None

        if env_name:
            node_env_dict[server_fqdn] = env_name
            msg = "Add/Modify map file with env_name %s for server %s" % (env_name, server_fqdn)
            self._smgr_log.log(self._smgr_log.DEBUG, msg)
        else:
            env_name = node_env_dict.pop(server_fqdn, None)
            msg = "Remove server from map file for server %s" % (server_fqdn)
            self._smgr_log.log(self._smgr_log.DEBUG, msg)
            if not env_name:
                return env_name

        try:
            with open(node_env_map_file, "w") as env_file:
                json.dump(node_env_dict, env_file, sort_keys = True,
                          indent = 4)
            # end with
        except:
            msg = "Not able open environment map file %s for update" % (node_env_map_file)
            self._smgr_log.log(self._smgr_log.ERROR, msg)
            return None
        return env_name
    # end update_node_map_file


    def provision_server(
        self, provision_params, server, cluster, cluster_servers):

        # The new way to create necessary puppet manifest files and parameters data.
        # The existing method is kept till the new method is well tested and confirmed
        # to be working.
        puppet_manifest_version = provision_params.get(
            'puppet_manifest_version', "")
        environment = puppet_manifest_version.replace('-','_')
        if ((environment != "") and
            (os.path.isdir(
                "/etc/puppet/environments/" + environment))):
            self.new_provision_server(
                provision_params, server, cluster, cluster_servers)
            return
        # end if puppet_manifest_version
        server_fqdn = provision_params["server_id"] + "." + \
            provision_params["domain"]
        env_name = "contrail_" + environment
        
        resource_data = ''
        # Create a new site file for this server
        server_manifest_file = self.pupp_create_server_manifest_file(
            provision_params)
        # Clear the variables that are used to compile the global
        # variables list
        self._params_dict.clear()
        data = '''node '%s.%s' {\n''' % (
            provision_params["server_id"],
            provision_params["domain"])
        if provision_params['setup_interface'] == "Yes":
            data += self.create_interface(provision_params)
            data += '''}'''
            # write the data to manifest file for this server.
            with open(server_manifest_file, 'w') as f:
                f.write(data)
            # Add entry for the server to environment mapping in 
            # node_mapping.json file.
	    try:
                node_env_dict = {}
                try:
                    with open(
                        self.smgr_base_dir+self._node_env_map_file,
	                "r") as env_file:
                        node_env_dict = json.load(env_file)
                    # end with
                except:
                    pass
                node_env_dict[server_fqdn] = env_name
                with open(
                    self.smgr_base_dir+self._node_env_map_file,
                    "w") as env_file:
                    json.dump(
                        node_env_dict, env_file,
                        sort_keys = True, indent = 4)
                # end with
	    except:
	        pass
            return

        if (provision_params['openstack_mgmt_ip'] == ''):
            contrail_openstack_mgmt_ip = provision_params["server_ip"]
        else:
            contrail_openstack_mgmt_ip = provision_params['openstack_mgmt_ip']
        contrail_storage_cluster_network = self.get_control_network_mask(provision_params,contrail_openstack_mgmt_ip)
        # Storage params added to the top of the manifest file
        resource_data += '''$contrail_host_roles= ['''
        for role in provision_params['host_roles']:
            resource_data += '''\"%s\",''' % (str(role))
        resource_data = resource_data[:len(resource_data)-1]+']'
        resource_data += '''\n'''
        resource_data += '''$contrail_storage_num_osd= %s\n''' % (provision_params['storage_num_osd'])
        resource_data += '''$contrail_storage_cluster_network= %s\n''' % (str(contrail_storage_cluster_network))
        resource_data += '''$contrail_storage_enabled= '%s'\n''' % (provision_params['contrail-storage-enabled'])
        resource_data += '''$contrail_live_migration_host = '%s'\n''' % (provision_params['live_migration_host'])
        resource_data += '''$contrail_live_migration_storage_scope = '%s'\n''' % (provision_params['live_migration_storage_scope'])

        # Create resource to have repository configuration setup on the
        # target
        resource_data += self._update_provision_start(provision_params)

	# update_system_config() is responsible for adding system configuration
	# e.g rsyslog.conf, configure UID/GID
        resource_data += self._update_system_config(provision_params)

        resource_data += self._repository_config(provision_params)

        resource_data += self._update_kernel(provision_params)

        # Always call common function for all the roles
        resource_data += self._roles_function_map["common"](self, provision_params)
        last_res_added =\
            "Contrail_%s::Contrail_common::Contrail_common[\"contrail_common\"]" %(
                provision_params['puppet_manifest_version'])

        # Iterate thru all the roles defined for this server and
        # call functions to add the necessary puppet lines in server.pp file.
        # list array used to ensure that the role definitions are added
        # in a particular order
        roles = ['database', 'openstack', 'config', 'control',
                 'collector', 'webui', 'zookeeper', 'compute', 'storage-compute', 'storage-master']
        for role in roles:
            if provision_params['roles'].get(role) and  provision_params['server_ip'] in \
                provision_params['roles'][role]:
                resource_data += self._roles_function_map[role](
                    self, provision_params, last_res_added)
            #if role == "config":
            #    last_res_added =  "Contrail-common::Haproxy-cfg[\"haproxy_cfg\"]"
                if role == "zookeeper":
                    last_res_added =  "Contrail_$s::Contrail_common::Contrail-cfg-zk[\"contrail_cfg_zk\"]" %(
                        provision_params['puppet_manifest_version'])
                elif role == "storage-master" or role == "storage-compute":
                    storage_role = "storage"
                    last_res_added = (
                        "Contrail_%s::Contrail_%s::Contrail_%s[\"contrail_%s\"]")\
                            % (provision_params['puppet_manifest_version'], storage_role, storage_role, storage_role)
                else:
                    last_res_added = (
                        "Contrail_%s::Contrail_%s::Contrail_%s[\"contrail_%s\"]")\
                            % (provision_params['puppet_manifest_version'], role, role, role)


        #Call stuff to be added at end
        if provision_params['execute_script']:
            resource_data += self.puppet_add_script_end_role(provision_params,
                                                    last_res_added)
            #TODO update last_res_added
            #last_res_added = 

        resource_data += self._update_provision_complete(provision_params,
                                                         last_res_added)

        # params_data and resource_data are compiled now. Add those to data and write
        # to manifest file for this server node.
        self._smgr_log.log(self._smgr_log.DEBUG, "param list")
        for key, value in self._params_dict.items():
            self._smgr_log.log(self._smgr_log.DEBUG, "%s = %s" % (key, value))
            data += ("$%s = %s\n" %(key, value))
        data += resource_data
        data += '''}'''
        # write the data to manifest file for this server.
        with open(server_manifest_file, 'w') as f:
            f.write(data)
        # Add entry for the server to environment mapping in 
        # node_mapping.json file.
	try:
            node_env_dict = {}
            try:
                with open(
                    self.smgr_base_dir+self._node_env_map_file,
	            "r") as env_file:
                    node_env_dict = json.load(env_file)
                # end with
            except:
                pass
            node_env_dict[server_fqdn] = env_name
            with open(
                self.smgr_base_dir+self._node_env_map_file,
                "w") as env_file:
                json.dump(
                    node_env_dict, env_file,
                    sort_keys = True, indent = 4)
            # end with
	except:
	    pass
    # end provision_server
# class ServerMgrPuppet

if __name__ == "__main__":
    pass
