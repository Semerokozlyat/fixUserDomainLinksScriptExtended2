#!/usr/bin/python

from optparse import OptionParser
import time
from Utils import *
from poaupdater import openapi
from poaupdater import apsapi
from poaupdater import uSysDB
import urllib2

O365_APS_TYPE_USER = "http://www.parallels.com/Office365/User/2.0"
O365_APS_TYPE_DOMAIN = "http://www.parallels.com/Office365/Domain/2.0"
O365_APS_TYPE_APP = "http://www.parallels.com/Office365"


# VBE: indentation
def getAccountToken(api, accId, subId):
    if accId == None:
        raise BaseException("Failed to get Account token: Account ID is not defined")

    if subId == None:
        raise BaseException("Failed to get Account token: Subscription ID is not defined")

    ret = api.pem.APS.getAccountToken(
            account_id=accId,
            subscription_id=subId
    )

    token = ret["aps_token"]
    return {"APS-Token": token}


def getSubscriptionToken(api, subId):
    if subId == None:
        # VBE: Account => Subscription
        raise BaseException("Failed to get Account token: Subscription ID is not defined")
    ret = api.pem.APS.getSubscriptionToken(
            subscription_id=subId
    )
    token = ret["aps_token"]
    return {"APS-Token": token}


def countInstanceResources(api, aps_api, Token, appInstanceId, apsResourceType):
    """ Count specific APS resource quantity, using the information from 'call' response headers.
Headers look like:
---
2017-04-24 13:02:46.347 [INFO] Response headers:
        Date: Mon, 24 Apr 2017 06:02:46 GMT
        Connection: Close
        Content-Range: items 0-0/1
...
---
    :rtype: object
"""

    respHeaders = {}

    path = "aps/2/resources/?implementing(%s)" \
           ",and(eq(aps.status,aps:ready))" \
           ",limit(0,1)" \
           % (apsResourceType)

    # VBE: the variable 'request' isn't used; the name 'response' is more appropriate.
    request = aps_api.call('GET', path, Token, None, None, respHeaders)
    # VBE: the validation of the value would be useful
    resourceCount = int(respHeaders['Content-Range'].split('/')[1])
    return resourceCount


def findAffectedUsers(appInstanceId):
    """ Finds 'affected users': when subdomain part of UPN and linked domain are different. """

    api = openapi.OpenAPI()
    aps_api = apsapi.API(getApsApiUrl())
    appInstanceToken = getAppInstanceToken(appInstanceId, api)
    instanceUsersCount = countInstanceResources(appInstanceId, O365_APS_TYPE_USER)
    affectedUsers = []

    path = "aps/2/resources/?implementing(%s)" \
           ",and(eq(aps.status,aps:ready)" \
           ",select(aps.id,login,domain.domain_name,tenant.aps.id)" \
           ",sort(+aps.id)" \
           ",limit(0,%d))" \
           % (O365_APS_TYPE_USER, instanceUsersCount)
    allInstanceUsers = aps_api.GET(path, appInstanceToken)
    for user in allInstanceUsers:
        # VBE: strange condition  user['domain']['domain_name']!=0   ----- a glupost of mine, it should be len(user['domain']['domain_name'])!=0
        if (user['domain']['domain_name'].lower() in user['login'].lower() and len(user['login']) != 0 and
                    user['domain']['domain_name'] != 0):
            # It is not necessary to log users which are OK  ------ there was an idea to show good\bad users ratio.
            log("Processing user " + user['login'] + ". He is OK: linked domain matches login.", logging.INFO, True)
        else:
            log("Processing user " + user['login'] + ". He is NOT OK: linked to domain with name: " + user['domain'][
                'domain_name'], logging.INFO, True)
            affectedUsers.append(user)
    return affectedUsers


def findAffectedUsersWrongDomainTenant(api, aps_api, Token, appInstanceId):
    """ Finds all 'affected' users: when user is linked to a domain, related to wrong Tenant resource,
    i.e. Domain is linked to Syndication subscription while account is migrated to CSP.
"""
    instanceUserCount = countInstanceResources(api, aps_api, Token, appInstanceId, O365_APS_TYPE_USER)
    instanceDomainsCount = countInstanceResources(api, aps_api, Token, appInstanceId, O365_APS_TYPE_DOMAIN)
    affectedUsers = {}
    domainTenantMap = {}
    usersMap = {}
    allDomainsList = []

    path = "aps/2/resources?implementing(%s)" \
           ",and(eq(aps.status,aps:ready))" \
           ",select(aps.id,domain_name,tenant.aps.id,cloud_status)" \
           ",limit(0,%d)" \
           % (O365_APS_TYPE_DOMAIN, instanceDomainsCount)
    allInstanceDomains = aps_api.GET(path, Token)
    for domain in allInstanceDomains:
        # Creating map:  {'domain APS UID': ['domain name','cloud status','Tenant APS UID'], ... }
        data = [str(domain['domain_name']), str(domain['cloud_status']), str(domain['tenant']['aps']['id']).lower()]
        domainTenantMap[str(domain['aps']['id'])] = data

        # List with all domains - to save time and not to ask APSC every time.
        allDomainsList.append(str(domain['domain_name']).lower())

    path2 = "aps/2/resources?implementing(%s)" \
            ",and(eq(aps.status,aps:ready))" \
            ",select(aps.id,login,tenant.aps.id,domain.aps.id)" \
            ",limit(0,%d)" \
            % (O365_APS_TYPE_USER, instanceUserCount)
    allInstanceUsers = aps_api.GET(path2, Token)
    for user in allInstanceUsers:
        # Creating map: {'user APS UID': ['user login', 'Tenant APS UID', 'linked domain APS UID'], ... }
        data2 = [str(user['login']).lower(), str(user['tenant']['aps']['id']), str(user['domain']['aps']['id'])]
        usersMap[str(user['aps']['id'])] = data2

    # Our maps (hint):
    # domainTenantMap = {'domain APS UID': ['domain name','cloud status','Tenant APS UID'], ... }
    # usersMap =        {'user APS UID':   ['user login', 'Tenant APS UID', 'linked domain APS UID'], ... }

    for key, val in usersMap.items():
        loginDomainPart = val[0].split('@')[1]
        userAPSTenant = val[1]
        userDomainUID = val[2]
        # Processing 2 maps: "usersMap" and "domainTenantMap".
        if loginDomainPart not in allDomainsList:
            log("Domain " + loginDomainPart + " has no Office365 service assigned.", logging.INFO, True)
        elif userAPSTenant == domainTenantMap[userDomainUID][2]:
            pass
            # log("User " + key + " is linked to domain with the same Tenant APS UID. Correct.", logging.INFO, True)
        elif not (userAPSTenant == domainTenantMap[userDomainUID][2]):
            log("User " + key + " and its linked domain has different Tenant APS UIDs. Tenant UID of domain is: " +
                domainTenantMap[userDomainUID][2] + ", Tenant UID of user is: " + userAPSTenant, logging.INFO, True)

            # Find the correct Domain APS UID to link with
            for k, v in domainTenantMap.items():
                if (userAPSTenant in v) and (loginDomainPart in v):
                    log("Correct domain UID for this affected user is: "+str(k), logging.INFO, True)
                    affectedUsers[key] = k
                else:
                    pass
        else:
            log("Situation with user " + str(key) + " is COMPLETELY UNEXPECTED. Check it!", logging.INFO, True)

    return affectedUsers


def findAllAffectedUsers (api, aps_api, Token, appInstanceId):
    """ Function to find all affected users. Criteria:
     1. User's subdomain login part and linked domain name are different;
     2. User and its linked domain are related to different Office365/Tenant resources
     """
    #usersInstanceTotalCount = countInstanceResources(api, aps_api, Token, appInstanceId, O365_APS_TYPE_USER)
    domainsInstanceTotalCount = countInstanceResources(api, aps_api, Token, appInstanceId, O365_APS_TYPE_DOMAIN)

    allInstanceDomainsMap = {}
    affectedUsers = {}

    path = "aps/2/resources/?implementing(%s)" \
           ",and(eq(aps.status,aps:ready))" \
           ",select(aps.id,login,tenant.aps.id,domain.aps.id)" \
           ",limit(0,50000)" \
           % (O365_APS_TYPE_USER)
           #% (O365_APS_TYPE_USER, usersInstanceTotalCount)
    
    try:
        allInstanceUsers = aps_api.GET(path, Token)
    except Exception as ex:
        log(str(ex), logging.INFO, True)        

    path = "aps/2/resources?implementing(%s)" \
           ",and(eq(aps.status,aps:ready))" \
           ",select(aps.id,domain_name,tenant.aps.id)" \
           ",limit(0,%d)" \
    % (O365_APS_TYPE_DOMAIN, domainsInstanceTotalCount)
    allInstanceDomains = aps_api.GET(path, Token)

    # Creating "allInstanceDomainsMap" dictionary.
    # Legend:   [APS UID of Office365/Domain resource]   [domain name] _ [APS UID of related Office365/Tenant]
    # Example: {'f2294c9a-3d50-4a48-89a5-214850d53e76': 'anotherdomain.com_eff56129-ccea-4b06-b754-4217eb4916ad'}
    for domain in allInstanceDomains:
	try:
		domainMetaData = str(domain['domain_name']).lower() + "_" + str(domain['tenant']['aps']['id'])
		allInstanceDomainsMap[str(domain['aps']['id'])] = domainMetaData
	except Exception as ex:
		log("Domain with UID" + domain['aps']['id'] + " does not contain some required fields.", logging.INFO, True)
		log(str(ex), logging.INFO, True)
		pass

    # Checking: taking domain record from "allInstanceDomainsMap" by key (domain APS UID) and comparing to
    #    user MetaData (subdomain part of user login + "_" + Tenant APS UID) for each instance users.
    # If something is different: user is affected, i.e. either linked to different Tenants or login subdomain part is
    #    not equal to the linked domain name.
    for user in allInstanceUsers:
        userMetaData = str(user['login'].split('@')[1]).lower() + "_" + str(user['tenant']['aps']['id'])

        if allInstanceDomainsMap[str(user['domain']['aps']['id'])] == userMetaData:
            #log("User " + user['login'] + " is linked correctly.", logging.INFO, True)
            pass
        else:
            log("User " + user['login'] + " is NOT correctly linked.", logging.INFO, True)
            if userMetaData not in allInstanceDomainsMap.values():
                #print "There is NO domain with name: " + userMetaData.split('_')[0] + " and Office365/Tenant resource UID: " + userMetaData.split('_')[1] + " exists at all! Please create it manually."
                log("There is NO domain with name: " + userMetaData.split('_')[0] + " and Office365/Tenant resource UID: " + userMetaData.split('_')[1] + " exists at all! Please create it manually.", logging.INFO, True)
                affectedUsers[str(user['aps']['id'])] = str("NO such domain: " + userMetaData.split('_')[0] + " with Tenant UID: " + userMetaData.split('_')[1])
            else:
                for key, val in allInstanceDomainsMap.items():
                    if val != userMetaData:
                        pass
                    else:
                        correctDomainUIDtoLink = key
                        log("CorrectDomainUIDtoLink: " + correctDomainUIDtoLink, logging.INFO, True)
                        affectedUsers[str(user['aps']['id'])] = correctDomainUIDtoLink
    return affectedUsers

def fixIncorrectDomainLink(api, aps_api, Token, userUIDToFix, correctDomainUIDToLink):
    """ Links an Office365/User resource to a given Office365/Domain. """

    path = "aps/2/resources/%s/domain/" % userUIDToFix
    body = {
        "aps": {"id": correctDomainUIDToLink},
    }
    try:
        aps_api.POST(path, Token, body)
    except Exception as ex:
        log("Failed to update domain link for user: " + userUIDToFix, logging.INFO, True)
        log(str(ex), logging.INFO, True)


def createOffice365DomainResource(appInstanceId, domainName, coreDomainUID, tenantAPSUID):
    """ Creates new Office365/Domain resource in scope of certain OA subscription. Core domain UID should be specified to link with. """

    api = openapi.OpenAPI()
    aps_api = apsapi.API(getApsApiUrl())
    appInstanceToken = getAppInstanceToken(appInstanceId, api)

    # VBE: Unfortunately it doesn't work     --------  it does work in terms of poaupdater module: helps to add additioanl request headers. Yes, it doesn't work in scope of this task (
    appInstanceToken[
        "APS-Resource-ID"] = tenantAPSUID  # <-- add additional header with Office365/Tenant APS resource UID. Need for proper linking.

    path2 = "aps/2/applications/"
    allApplications = aps_api.GET(path2,
                                  appInstanceToken)  # <-- try to find Application UID by package name. RQL doesn't work on /applications/ node.
    for application in allApplications:
        if application.aps.package.name == 'Office 365':
            applicationUID = str(application.aps.id)
            # VBE: break   -------- a glupost of mine, forgot to add it

    # VBE: Need to validate the value of applicationUID
    path = "aps/2/applications/%s/office365domains/" % applicationUID
    # VBE: the body should be constructed from using the existing domain resource (including service_name, dns_records etc.)
    body = {
        "aps": {
            "type": O365_APS_TYPE_DOMAIN
        },
        "domain_name": domainName,
        "cloud_status": "Ready",
        "service_name": "rapidAuth,mobileDevice",
        "domain": {
            "aps": {
                "id": coreDomainUID
            }
        }
    }
    try:
        aps_api.POST(path, appInstanceToken, body)
    except Exception as ex:
        log("Failed to create new domain with name: " + domainName, logging.INFO, True)


def main():
    parser = OptionParser(version=VERSION,
                          usage="\nFind users whose UPN differs from domain name they are linked with. Fix by linking them to a correct domain in scope of the same subscription.\n\n  Usage: %prog --app-instance-id ID [--dry-run]")
    parser.add_option("--app-instance-id", dest="app_instance_id", type="int",
                      help="Office 365 APS 2.0 Application Instance ID")
    #parser.add_option("--mode", dest="mode", type="string", default=None,
    #                  help="Script mode. Possible values: \n fixByDomainName - fix User <-> Domain links when login subdomain part does not match linked domain name; \n fixByTenantUID - fix User <-> Domain links when user and his domain are linked to different Tenant resources;")
    parser.add_option("--dry-run", dest="dry_run", action="store_true",
                      help="Dry-run mode: count affected users and create a report only")

    (options, args) = parser.parse_args()

    if not options.app_instance_id:
        parser.print_help()
        raise Exception("The required parameter 'app-instance-id' is not specified.")

    else:
        # init globals
        date_for_file_name = time.strftime("%Y%m%d-%H%M%S")
        logfile_name = "./fixUserDomainLinks_" + date_for_file_name + "_O365_instance_" + str(options.app_instance_id) + ".log"
        format_str = "%(asctime)s   %(levelname)s   %(message)s"
        logging.basicConfig(filename=logfile_name, level=logging.DEBUG, format=format_str)

        initEnv()
        api = openapi.OpenAPI()
        aps_api = apsapi.API(getApsApiUrl())
        appInstanceToken = getAppInstanceToken(options.app_instance_id, api)

        dtStart = datetime.datetime.now()

        instanceUsersCount = countInstanceResources(api, aps_api, appInstanceToken, options.app_instance_id,
                                                    O365_APS_TYPE_USER)  # <-- count instance users, using the response headers

        log(" --- Processing application instance " + str(options.app_instance_id) + "---\n", logging.INFO, True)
        affectedUsers = findAllAffectedUsers(api, aps_api, appInstanceToken, options.app_instance_id)
        log(" \n", logging.INFO, True)
        log(" --- SUMMARY about application instance " + str(options.app_instance_id) + "---\n", logging.INFO, True)
        log(" Total instance users: " + str(instanceUsersCount), logging.INFO, True)
        log(" Total affected users: " + str(len(affectedUsers)), logging.INFO, True)
        log(" \n", logging.INFO, True)
        log(" Affected users are (User APS UID \ Correct Domain APS UID, if any): ", logging.INFO, True)
        for user in affectedUsers.items():
            log(user, logging.INFO, True)
        log(" \n", logging.INFO, True)
        if not options.dry_run:           # <----- dry_run option
            log(" --- Fixing affected users. ", logging.INFO, True)
            for key, value in affectedUsers.items():
                if "NO such" not in value:
                    #log("Trying to fix user: " + str(key), logging.INFO, True)
                    fixIncorrectDomainLink(api, aps_api, appInstanceToken, key, value)
        else:
            log("Dry-run mode, links were not fixed actually. ", logging.INFO, True)

        totalExecutionTime = TimeProfiler.convertTimedelta2Milliseconds(datetime.datetime.now() - dtStart)
        log(" \n", logging.INFO, True)
        log(" Total execution time: " + str(totalExecutionTime) + " milliseconds.\n", logging.INFO, True)

"""
        # Mode to fix users by Office365/Tenant APS resource UID consistence:
        if options.mode == 'fixByTenantUID':
            affectedUsersCorrectDomainPairs = findAffectedUsersWrongDomainTenant(api, aps_api, appInstanceToken, options.app_instance_id)
            if not options.dry_run:  # <--  --dry-run option
                for userUID, correctDomainUID in affectedUsersCorrectDomainPairs.items():
                    fixIncorrectDomainLink(userUID, correctDomainUID, options.app_instance_id)


        # Mode to fix users by Office365/Domain name property
        if options.mode == 'fixByDomainName' and (instanceUsersCount >= 0):
            affectedUsers = findAffectedUsers(options.app_instance_id)   #<-- find all affected users, where UPN does not match domain linked
"""

"""

        instanceUsersCount = countInstanceResources(options.app_instance_id,O365_APS_TYPE_USER) #<-- count instance users, using the response headers
        log(" --- Application instance " + str(options.app_instance_id) + " contains " + str(instanceUsersCount) + " users total. ---\n", logging.INFO, True)

        if instanceUsersCount:
            affectedUsers = findAffectedUsers(options.app_instance_id)#<-- find all affected users, where UPN does not match domain linked

        # VBE: affectedUsers can be undefined   ------ there was an additional indent: "if instanceUsersCount:" condition covered this, but its gone
        log(" --- Application instance " + str(options.app_instance_id) + " contains " + str(len(affectedUsers)) + " AFFECTED user(s) total: ---\n", logging.INFO, True)
        for user in affectedUsers:
            log(user['login'], logging.INFO, True)

        log(" --- Fixing affected users. --- \n", logging.INFO, True)
        for user in affectedUsers:
            counter = 0
            log("Trying to fix " + user['login'], logging.INFO, True)
            desiredDomainName = user['login'].split('@')[1]
            path2 = "aps/2/resources/?implementing(%s)" \
                ",and(eq(aps.status,aps:ready)" \
                ",and(eq(aps.subscription,%s))" \
                ",and(eq(domain_name,%s))" \
                ",select(aps.id)" \
                ",limit(0,1))" \
                % (O365_APS_TYPE_DOMAIN,user['aps']['subscription'],desiredDomainName) #<--- try to find Office365/Domain resource with desired name in scope of the same subscription
            correctDomainToLink = aps_api.GET(path2, appInstanceToken)
            userUIDToFix = str(user['aps']['id'])
            userTenantAPSUID = str(user['tenant']['aps']['id'])

            if correctDomainToLink:
                correctDomainUIDToLink = str(correctDomainToLink[0].aps.id)
                log("Correct domain UID to link with is: " + correctDomainUIDToLink + "\n", logging.INFO, True)
                if not options.dry_run:      #<--  --dry-run option
                    fixIncorrectDomainLink(userUIDToFix,correctDomainUIDToLink,options.app_instance_id)
            else:
                # VBE: It will be better to print all such errors together in the summary  ------- yes
                log("Cannot find domain with name " + desiredDomainName + " in scope of subscription " + user['aps']['subscription'] + ". Please add it manually.\n", logging.INFO, True)

            totalExecutionTime = TimeProfiler.convertTimedelta2Milliseconds(datetime.datetime.now() - dtStart)
            log("\n\nTotal execution time: %s" % millisecondsToStr(totalExecutionTime), logging.INFO, True)
            counter += 1
            #print "Counter= ",counter
            # VBE the condition len(affectedUsers) > 500 is redundant  ------- yes
            if (len(affectedUsers) > 500 and counter % 500 == 0):
                log("Refreshing APS Instance Token", logging.INFO, True)
                appInstanceToken = getAppInstanceToken(options.app_instance_id, api)
"""

"""
This part is commented-out: an attempt to create new Office365/Domain resources leads to the following error:
---
"error": "APS::Util::AccessViolation",
"message": "Association with the resource 'eb1a5b99-cc59-4773-8d25-d583b2c9a60c' is not allowed."
---
Looks like it is allowed with application certificate only.
Script now only reports about these cases.

                log("Trying to create new Office365/Domain with domain name: " + desiredDomainName, logging.INFO, True)
                path2 = "aps/2/resources/?implementing(%s)" \
                    ",and(eq(aps.status,aps:ready)" \
                    ",and(eq(domain_name,%s))" \
                    ",select(aps.id,domain.aps.id)" \
                    ",limit(0,1))" \
                    % (O365_APS_TYPE_DOMAIN,desiredDomainName) #<--- try to find a "template" Office365/Domain resource with desired domain name to take its link to core domain
                templateDomain = aps_api.GET(path2, appInstanceToken)
                coreDomainUID = str(templateDomain[0]['domain']['aps']['id'])
                if not coreDomainUID:
                    log("NO such domain. Please add domain " + desiredDomainName + " to the Office365.", logging.INFO, True)
                else:
                    log("Core domain UID is: " + coreDomainUID, logging.INFO, True)  #<-- we need UID of core domain resource to specify it upon Office365/Domain resource creation - to link with.
                    if not options.dry_run:    #<--  --dry-run option
                    createOffice365DomainResource(options.app_instance_id,desiredDomainName,coreDomainUID,userTenantAPSUID)

                #refresh token here
                counter += 1
                #print "Counter= ",counter
                if (len(affectedUsers) > 500 and counter % 500 == 0):
                    log("Refreshing APS Instance Token", logging.INFO, True)
                    appInstanceToken = getAppInstanceToken(options.app_instance_id, api)
"""

if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        log("Unexpected error: %s" % ex, logging.ERROR, True)
        # traceback.print_exc()
        sys.exit(1)
