#!/bin/bash

# The purpose of this script is to be used in a GitHub Actions workflow to set up a Minikube cluster for testing the individual NaaVRE service such as the /NaaVRE-workflow-servic, /NaaVRE-containerizer-service, etc.
# It assumes that Minikube is already installed and running, and that kubectl and helm are also installed.
# It adds minikube IP to /etc/hosts, adds the necessary Helm repositories, installs the NaaVRE Helm chart, and sets up the necessary environment variables for testing.


# Usage: ./setup-tests.sh -f <values-file>
# Example: ./setup-tests.sh -f values/minikube-values-deploy-naavre-containerizer-service-minikube-github-secrets.yaml

# For example values file, see values/ in this repository.

print_usage() {
  echo "Usage: $0 -f <values-file>"
  echo "Example: $0 -f values/minikube-values-deploy-naavre-containerizer-service-minikube-github-secrets.yaml"
  echo "Options:"
  echo "  -f, --values        Path to the Helm values file to use for deployment"
  echo "  -d, --delete-naavre-dir  Delete the NaaVRE-helm directory before cloning it again"
  echo "  -n, --delete-namespace        Delete the NaaVRE namespace before installation"
  echo "  -u, --uninstall-naavre        Uninstall NaaVRE before installation"
  echo "  -p, --delete-pv-pvc        Delete PV and PVC before creating them again"
  echo "  -v, --deploy-naavre        Deploy NaaVRE "
  echo "  -c , --chart-file       Path to the NaaVRE Helm Chart.yaml file "
  exit 1
}


set -e
if [ -z "$GITHUB_ENV" ]; then
  GITHUB_ENV=/dev/stdout
fi

VALUES_FILE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -f|--values)
      VALUES_FILE="$2"
      shift # past argument
      shift # past value
      ;;
    -d|--delete-naavre-dir)
      DELETE_NAAAVRE_DIR="true"
      shift # past argument
      ;;
    -n|--delete-namespace)
      DELETE_NAMESPACE="true"
      shift # past argument
      ;;
    -u|--uninstall-naavre)
      UNINSTALL_NAAAVRE="true"
      shift # past argument
      ;;
    -p|--delete-pv-pvc)
      DELETE_PV_PVC="true"
      shift # past argument
      ;;
    -v |--deploy-naavre)
      DEPLOY_NAAAVRE="true"
      shift # past argument
      ;;
    -c |--chart-file)
      EXTRA_CHART_FILE="$2"
      shift # past argument
      shift # past value
      ;;
    -*|--*)
      echo "Unknown option $1"
      exit 1
      ;;
    *)
      print_usage
      shift # past argument
      ;;
  esac
done

# If no values file is provided print help.
if [[ -z "$VALUES_FILE" ]]; then
  echo "No values file provided. Using default values file: values/minikube-values.yaml"
  print_usage
  exit 1
else
  # Check if the values file exists
  if [[ ! -f "$VALUES_FILE" ]]; then
    echo "Values file $VALUES_FILE does not exist."
    exit 1
  fi
fi

if [[ -n "$VALUES_FILE" ]]; then
  echo "Using values file: $VALUES_FILE"
fi

# If $EXTRA_CHART_FILE exists, set its absolute path
if [[ -f "$EXTRA_CHART_FILE" ]]; then
  EXTRA_CHART_FILE=$(realpath "$EXTRA_CHART_FILE")
  echo "Using chart file: $EXTRA_CHART_FILE"
fi

# Get only the last part of the current directory
CURRENT_DIR=$(basename "$(pwd)")

if [ "$CURRENT_DIR" != "NaaVRE-helm" ]; then
  RUNS_FROM_NAAAVRE_HELM="false"
else
  RUNS_FROM_NAAAVRE_HELM="true"
fi

# Variables
context="minikube"
namespace="naavre"

export MINIKUBE_HOST="naavre-dev.minikube.test"
export MINIKUBE_S3_HOST="s3.naavre-dev.minikube.test"
export AUTH_TOKEN=""
export ARGO_TOKEN=""
export CLIENT_ID=naavre
export REALM=vre
export DISABLE_OAUTH=False
export OIDC_CONFIGURATION_URL="https://$MINIKUBE_HOST/auth/realms/$REALM/.well-known/openid-configuration"
export VERIFY_SSL="False"
export DISABLE_AUTH="False"
export CONFIG_FILE_URL="$CURRENT_DIR/minikube_configuration.json"
export SECRETS_CREATOR_API_ENDPOINT="https://$MINIKUBE_HOST/k8s-secret-creator/1.0.0"
export "SECRETS_CREATOR_SECRET_NAME=$namespace-k8s-secret-creator"
export ARGO_SERVICE_ACCOUNT_EXECUTOR="argo-executor"
export ARGO_VRE_API_SERVICE_ACCOUNT="argo-vreapi"
export ARGO_SERCERT_TOKEN_NAME=argo-vreapi.service-account-token



setup_minikube(){
  echo "Setting up Minikube"
  #Get the minikube IP and add it to /etc/hosts if not already present
  MINIKUBE_IP=$(minikube ip)
  export MINIKUBE_IP
  if ! grep -q "$MINIKUBE_IP" /etc/hosts; then
      echo "Adding minikube IP to /etc/hosts"
      echo "$MINIKUBE_IP $MINIKUBE_HOST" | sudo tee -a /etc/hosts > /dev/null
      echo "$MINIKUBE_IP $MINIKUBE_S3_HOST" | sudo tee -a /etc/hosts > /dev/null
  fi

  # Configure in-cluster DNS resolution for ingress-dns (https://minikube.sigs.k8s.io/docs/handbook/addons/ingress-dns/)
  cm=$(kubectl  -n kube-system get configmap/coredns -o json | jq ".data.Corefile += \"\ntest:53 {\n    errors\n    cache 30\n    forward . $MINIKUBE_IP\n}\"" | jq 'del(.metadata)')
  kubectl -n kube-system patch configmap/coredns --type merge -p "$cm"

  # Test $MINIKUBE_HOST
  minikube_status=$(minikube status --format '{{.Host}}')
  if [ "$minikube_status" != "Running" ]; then
      echo "Minikube is not running. Please start minikube and try again."
      exit 1
  fi

  minikube_http_code=$(curl -o /dev/null -s -w "%{http_code}" -k https://$MINIKUBE_HOST)
  echo "Minikube host $MINIKUBE_HOST returned HTTP status code $minikube_http_code"

  if ! [[ "$minikube_http_code" -ge 200 || "$minikube_http_code" -lt 500 ]]; then
      echo "Minikube local test failed"
      exit 1
  fi
}

deploy_naavre(){
  if [ "$RUNS_FROM_NAAAVRE_HELM" == "false" ]; then
    if [ "$DELETE_NAAAVRE_DIR" == "true" ]; then
      rm -rf NaaVRE-helm
    fi
    git clone https://github.com/NaaVRE/NaaVRE-helm.git
    cd NaaVRE-helm
    cp "../$VALUES_FILE" .
    cp "../$VALUES_FILE" secrets-minikube.yaml
  elif [ "$RUNS_FROM_NAAAVRE_HELM" == "true" ]; then
    cp "$VALUES_FILE" secrets-minikube.yaml
  fi

  # if EXTRA_CHART_FILE is provided, merge with naavre/chart
  if [ -f "$EXTRA_CHART_FILE" ]; then
    echo "Merging $EXTRA_CHART_FILE with naavre/Chart.yaml"
    while IFS=$'\t' read -r dep_name dep_version; do
      export DEP_NAME="$dep_name"
      export DEP_VERSION="$dep_version"
      echo "Processing dependency: $DEP_NAME with version $DEP_VERSION"
      yq -i '(.dependencies[] | select(.name == strenv(DEP_NAME) ) | .version) = strenv(DEP_VERSION)' naavre/Chart.yaml
    done < <(yq -r '.dependencies[] | [.name, .version] | @tsv' "$EXTRA_CHART_FILE")
  fi
  cd naavre && helm dependency update && cd ../
  # Add the third-party Helm repos
  if [ "$DEPLOY_NAAAVRE" == "true" ]; then
    ./deploy.sh repo-add
  fi
  # Read CELL_GITHUB_TOKEN from dev.env if it exists
  if [ -f "../dev.env" ]; then
    source ../dev.env
  fi


  #Reaplce cell_github_token in the values file with the value from the environment variable CELL_GITHUB_TOKEN if it exists
  if [ -n "$CELL_GITHUB_TOKEN" ]; then
    export CELL_GITHUB_TOKEN=$CELL_GITHUB_TOKEN
    yq e -i '.jupyterhub.vlabs.openlab.configuration.cell_github_token = strenv(CELL_GITHUB_TOKEN)' "secrets-minikube.yaml"
  fi

  if [ "$DELETE_NAMESPACE" == "true" ]; then
    kubectl delete ns $namespace --ignore-not-found=true
  fi
  if [ "$UNINSTALL_NAAAVRE" == "true" ]; then
    ./deploy.sh --kube-context minikube -n "$namespace" uninstall || true
  fi
    ./deploy.sh --kube-context "$context" -n "$namespace" install-keycloak-operator
  if [ "$DEPLOY_NAAAVRE" == "true" ]; then
    ./deploy.sh --kube-context "$context" -n "$namespace" -f values/values-deploy-minikube.yaml -f "secrets-minikube.yaml" install
  fi
  rm secrets-minikube.yaml
  # Exit if the installation fails
  if [ $? -ne 0 ]; then
      echo "Helm installation failed"
      exit 1
  fi
  if [ "$RUNS_FROM_NAAAVRE_HELM" == "false" ]; then
    cd ../
  fi
}

get_auth_token(){
  AUTH_TOKEN=$(curl -s -k -X POST "https://$MINIKUBE_HOST/auth/realms/$REALM/protocol/openid-connect/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "grant_type=password" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "username=$USERNAME" \
    --data-urlencode "password=$USER_PASSWORD" \
    --data-urlencode "scope=openid"| jq -r '.access_token')

  # Make sure AUTH_TOKEN is not empty
  if [ -z "$AUTH_TOKEN" ]; then
      echo "Failed to get AUTH_TOKEN"
      exit 1
  fi
  export AUTH_TOKEN
}

setup_authentication() {
  #Get user access token for the workflow service and set the environment variable AUTH_TOKEN
  # Wait for https://$MINIKUBE_HOST/auth/realms/$REALM/.well-known/openid-configuration to be available and fail if it is not available
  timeout=700
  start_time=$(date +%s)
  while true; do
      if curl -k --silent --fail https://$MINIKUBE_HOST/auth/realms/$REALM/; then
          break
      fi
      current_time=$(date +%s)
      elapsed_time=$((current_time - start_time))
      if [ $elapsed_time -ge $timeout ]; then
          echo "OIDC configuration URL" https://$MINIKUBE_HOST/auth/realms/$REALM/ "is not available"
          exit 1
      fi
      sleep 6
  done
  # Get credentials from secrets
  export USERNAME=$(kubectl get secret $namespace-keycloak-vre-realm -o=jsonpath={.data}  -n $namespace | jq -r '."vre-realm.json"' | base64 --decode |  jq -r '.users[0].username')
  if [ -z "$USERNAME" ]; then
      echo "USERNAME is empty. Please check the values file."
      exit 1
  fi
  export USER_EMAIL=$USERNAME@nowhere.no
  export USER_FIRST_NAME=$USERNAME
  export USER_LAST_NAME=$USERNAME

  export USER_PASSWORD=$(kubectl get secret $namespace-keycloak-vre-realm -o=jsonpath={.data}  -n $namespace | jq -r '."vre-realm.json"' | base64 --decode |  jq -r '.users[0].credentials[0].value')
  if [ -z "$USER_PASSWORD" ]; then
      echo "USER_PASSWORD is empty. Please check the values file."
      exit 1
  fi

  export KEYCLOAK_AMIN_USER=$(kubectl get secret $namespace-keycloak-admin -o=jsonpath={.data.username}  -n $namespace | base64 --decode)
  if [ -z "$KEYCLOAK_AMIN_USER" ]; then
      echo "KEYCLOAK_AMIN_USER is empty. Please check the values file."
      exit 1
  fi
  export KEYCLOAK_ADMIN_PASSWORD=$(kubectl get secret $namespace-keycloak-admin -o=jsonpath={.data.password}  -n $namespace | base64 --decode)
  if [ -z "$KEYCLOAK_ADMIN_PASSWORD" ]; then
      echo "KEYCLOAK_ADMIN_PASSWORD is empty. Please check the values file."
      exit 1
  fi

  # Get admin token
  KEYCLOAK_ADMIN_TOKEN=$(curl -s -k -X POST "https://$MINIKUBE_HOST/auth/realms/master/protocol/openid-connect/token" -H "Content-Type: application/x-www-form-urlencoded" --data-urlencode "grant_type=password"   --data-urlencode "client_id=admin-cli"   --data-urlencode "username=$KEYCLOAK_AMIN_USER"   --data-urlencode "password=$KEYCLOAK_ADMIN_PASSWORD" | jq -r '.access_token')
  if [ -z "$KEYCLOAK_ADMIN_TOKEN" ] || [ "$KEYCLOAK_ADMIN_TOKEN" == "null" ]; then
    echo "Failed to get admin token"
    exit 1
  fi

  #update realm token lifespan
  REALM_SETTINGS=$(curl -s -k -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    "https://$MINIKUBE_HOST/auth/admin/realms/$REALM")

  UPDATED_REALM=$(echo "$REALM_SETTINGS" | jq '.accessTokenLifespan = 36000 | .accessTokenLifespanForImplicitFlow = 36000')

  curl -s -k -X PUT "https://$MINIKUBE_HOST/auth/admin/realms/$REALM" \
    -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_REALM"


  # find user id
  USER_ID=$(curl -s -k -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/users?username=$USERNAME" | jq -r '.[0].id')

  if [[ -z "$USER_ID" || "$USER_ID" == "null" ]]; then
    echo "User $USERNAME not found"
    exit 1
  fi

  # fetch user JSON and clear required actions + mark verified/enabled
  USER_JSON=$(curl -s -k -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/users/$USER_ID")

  UPDATED_JSON=$(echo "$USER_JSON" | jq --arg email "$USER_EMAIL" --arg first "$USER_FIRST_NAME" --arg last "$USER_LAST_NAME" \
    '.requiredActions = [] |
     .emailVerified = true |
     .enabled = true |
     .email = (if $email == "" then .email else $email end) |
     .firstName = (if $first == "" then .firstName else $first end) |
     .lastName = (if $last == "" then .lastName else $last end)')

  curl -s -k -X PUT "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/users/$USER_ID" \
    -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_JSON"

  # ensure password is set and not temporary
  curl -s -k -X PUT "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/users/$USER_ID/reset-password" \
    -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"password\",\"temporary\":false,\"value\":\"$USER_PASSWORD\"}"


  #Find Keycloak internal client UUID
  CLIENT_UUID=$(curl -s -k -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/clients?clientId=$CLIENT_ID" | jq -r '.[0].id')

  if [ -z "$CLIENT_UUID" ] || [ "$CLIENT_UUID" == "null" ]; then
    echo "Client $CLIENT_ID not found in realm $REALM"
    exit 1
  fi

  #Get full client JSON, set directAccessGrantsEnabled=true and PUT it back
  CLIENT_JSON=$(curl -s -k -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/clients/$CLIENT_UUID")

  UPDATED_JSON=$(echo "$CLIENT_JSON" | jq '.directAccessGrantsEnabled = true')

  curl -s -k -X PUT "https://$MINIKUBE_HOST/auth/admin/realms/$REALM/clients/$CLIENT_UUID" \
    -H "Authorization: Bearer $KEYCLOAK_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_JSON"
  get_auth_token
}

get_argo_token(){
  #Get Argo workflow summation token and set it to configuration.json
  ARGO_TOKEN="$(kubectl get secret ${ARGO_SERCERT_TOKEN_NAME} -o=jsonpath='{.data.token}' -n $namespace | base64 --decode)"
  # Make sure ARGO_TOKEN is not empty
  if [ -z "$ARGO_TOKEN" ]; then
      echo "Failed to get Argo workflow submission token"
      exit 1
  fi
}

setup_argo(){
  get_argo_token
  # Wait for the Argo workflow service to be available
  timeout=200
  start_time=$(date +%s)
  while true; do
      if curl -k --silent --fail https://$MINIKUBE_HOST/argowf/; then
          break
      fi
      current_time=$(date +%s)
      elapsed_time=$((current_time - start_time))
      if [ $elapsed_time -ge $timeout ]; then
          echo "Argo workflow service at " https://$MINIKUBE_HOST/argowf/ "is not available"
          # Find the pods that are not running in the $namespace namespace and contain the word argo. Then print their status logs and describe them
          kubectl get pods -n $namespace | grep argo | grep -v Running
          for pod in $(kubectl get pods -n $namespace | grep argo | grep -v Running | awk '{print $1}'); do
              echo "Logs for pod $pod:"
              kubectl logs $pod -n $namespace
              echo "Describe for pod $pod:"
              kubectl describe pod $pod -n $namespace
          done
          exit 1
      fi
      sleep 5
  done


  # Test if the ARGO_TOKEN works on https://$MINIKUBE_HOST/argowf
  status_code=$(curl -o /dev/null -s -w "%{http_code}" -k "https://$MINIKUBE_HOST/argowf/api/v1/workflows/$namespace" -H "Authorization: Bearer $ARGO_TOKEN")
  echo "Argo API returned status code $status_code"
  if [ "$status_code" -ne 200 ]; then
      echo "Argo API returned status code $status_code"
      exit 1
  fi


  export ARGO_TOKEN
  # Wait for the executor service account to be created
  timeout=200
  start_time=$(date +%s)
  while true; do
      if kubectl get serviceaccount $ARGO_SERVICE_ACCOUNT_EXECUTOR -n $namespace > /dev/null 2>&1; then
          break
      fi
      current_time=$(date +%s)
      elapsed_time=$((current_time - start_time))
      if [ $elapsed_time -ge $timeout ]; then
          echo "Service account $ARGO_SERVICE_ACCOUNT_EXECUTOR is not available"
          exit 1
      fi
      sleep 5
  done

  # Wait for $ARGO_VRE_API_SERVICE_ACCOUNT service account to be created
  timeout=200
  start_time=$(date +%s)
  while true; do
      if kubectl get serviceaccount $ARGO_VRE_API_SERVICE_ACCOUNT -n $namespace > /dev/null 2>&1; then
          break
      fi
      current_time=$(date +%s)
      elapsed_time=$((current_time - start_time))
      if [ $elapsed_time -ge $timeout ]; then
          echo "Service account $ARGO_VRE_API_SERVICE_ACCOUNT is not available"
          exit 1
      fi
      sleep 5
  done
}

create_pv_pvc(){
  if [ -z "$namespace" ]; then
    echo "Namespace is not set. Please set the namespace variable."
    exit 1
  fi

  # Delete existing PV and PVC with the same name if they exist
  if [ "$DELETE_PV_PVC" == "true" ]; then
    while read volume_name; do
      kubectl delete pvc $volume_name -n $namespace --ignore-not-found=true
      kubectl delete pv  $volume_name -n $namespace --ignore-not-found=true
    done < volume_names
  fi
  while read volume_name; do
    kubectl apply -f - <<EOF
      apiVersion: v1
      kind: PersistentVolume
      metadata:
        name: $volume_name
      spec:
        accessModes:
          - ReadWriteMany
        capacity:
          storage: 5Gi
        hostPath:
          path: /tmp/$volume_name
EOF
    kubectl apply -f - <<EOF
      apiVersion: v1
      kind: PersistentVolumeClaim
      metadata:
        name: $volume_name
        namespace: $namespace
      spec:
        accessModes:
          - ReadWriteMany
        resources:
          requests:
            storage: 5Gi
EOF
  kubectl create -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: test-nginx-$volume_name
  namespace: $namespace
spec:
  containers:
   - name: csi-s3-test-nginx
     image: nginx
     volumeMounts:
       - mountPath: /usr/share/nginx/html/s3
         name: webroot
  securityContext:
    fsGroup: 100
  volumes:
   - name: webroot
     persistentVolumeClaim:
       claimName: $volume_name
       readOnly: false
EOF

  echo kubectl get pvc $volume_name -n $namespace
  kubectl get pvc $volume_name -n $namespace
  echo kubectl get pv $volume_name -n $namespace
  kubectl get pv $volume_name -n $namespace
  echo kubectl exec -it test-nginx-$volume_name -n $namespace  -- ls /usr/share/nginx/html/s3
  if [ $? -ne 0 ]; then
      echo "Failed to access the mounted volume in the test pod for volume: $volume_name"
      exit 1
  fi
  kubectl delete pod test-nginx-$volume_name -n $namespace --ignore-not-found=true
  done < volume_names
#  rm volume_names

}

setup_configuration_json(){

# Get the SECRETS_CREATOR_API_TOKEN from the secret created in the cluster and set it to the environment variable SECRETS_CREATOR_API_TOKEN
SECRETS_CREATOR_API_TOKEN="$(kubectl get secret ${SECRETS_CREATOR_SECRET_NAME} -o=jsonpath='{.data.API_TOKEN}' -n $namespace | base64 --decode)"
export SECRETS_CREATOR_API_TOKEN="$SECRETS_CREATOR_API_TOKEN"

export SECRETS_CREATOR_API_ENDPOINT="$SECRETS_CREATOR_API_ENDPOINT"

# Build minikube_configuration.json environment values
if [ -f "$VALUES_FILE" ]; then
    echo "Building minikube_configuration.json from $VALUES_FILE"
else
    VALUES_FILE=../$VALUES_FILE
fi

export BASE_IMAGE_TAGS_URL=$(yq e '.jupyterhub.vlabs.openlab.configuration.base_image_tags_url' "$VALUES_FILE")
if [ -z "$BASE_IMAGE_TAGS_URL" ]; then
    echo "BASE_IMAGE_TAGS_URL is empty. Please check the values file."
    exit 1
fi

export BASE_IMAGE_TAGS_URL=$(yq e '.jupyterhub.vlabs.openlab.configuration.base_image_tags_url' "$VALUES_FILE")
if [ -z "$BASE_IMAGE_TAGS_URL" ]; then
    echo "BASE_IMAGE_TAGS_URL is empty. Please check the values file."
    exit 1
fi
export MODULE_MAPPING_URL=$(yq e '.jupyterhub.vlabs.openlab.configuration.module_mapping_url' "$VALUES_FILE")

if [ -z "$MODULE_MAPPING_URL" ]; then
    echo "MODULE_MAPPING_URL is empty. Please check the values file."
    exit 1
fi

export CELL_GITHUB_URL=$(yq e '.jupyterhub.vlabs.openlab.configuration.cell_github_url' "$VALUES_FILE")
if [ -z "$CELL_GITHUB_URL" ]; then
    echo "CELL_GITHUB_URL is empty. Please check the values file."
    exit 1
fi

export REGISTRY_URL=$(yq e '.jupyterhub.vlabs.openlab.configuration.registry_url' "$VALUES_FILE")
if [ -z "$REGISTRY_URL" ]; then
    echo "REGISTRY_URL is empty. Please check the values file."
    exit 1
fi

# if dev.env file exists, source it to get CELL_GITHUB_TOKEN
if [ -f "dev.env" ]; then
  source dev.env
fi

if [ "$RUNS_FROM_NAAAVRE_HELM" == "true" ]; then
  CONFIG_JSON_PATH="../configuration.json"
elif [ "$RUNS_FROM_NAAAVRE_HELM" == "false" ]; then
  CONFIG_JSON_PATH="configuration.json"
fi

cat $CONFIG_JSON_PATH

# if configuration.json exists add the values, else skip
if [ -f "$CONFIG_JSON_PATH" ]; then
  export VIRTUAL_LAB_NAME="${VIRTUAL_LAB_NAME:-openlab}"
  jq --arg token "$ARGO_TOKEN" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .wf_engine_config.access_token = $token else . end)' $CONFIG_JSON_PATH > tmp.json && mv tmp.json minikube_configuration.json
  # Set namespace in minikube_configuration.json in the openlab
  jq --arg namespace "$namespace" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .wf_engine_config.namespace = $namespace else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  # Set service_account in minikube_configuration.json in the openlab
  jq --arg service_account "$ARGO_SERVICE_ACCOUNT_EXECUTOR" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .wf_engine_config.service_account = $service_account else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  # Set the cell_github_token in minikube_configuration.json in the virtual_lab_
  jq --arg cell_github_token "$CELL_GITHUB_TOKEN" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .cell_github_token = $cell_github_token else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  jq --arg cell_github_url "$CELL_GITHUB_URL" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .cell_github_url = $cell_github_url else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  jq --arg base_image_tags_url "$BASE_IMAGE_TAGS_URL" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .base_image_tags_url = $base_image_tags_url else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  jq --arg module_mapping_url "$MODULE_MAPPING_URL" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .module_mapping_url = $module_mapping_url else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  jq --arg registry_url "$REGISTRY_URL" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .registry_url = $registry_url else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  # Create a PV and PVC volume mount from the extraVolumeMounts in minikube_configuration.json
  # Save the name of the extraVolumeMounts in a bash array
  jq . minikube_configuration.json | jq -r --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations[] | select(.name == $vl) | .wf_engine_config.extraVolumeMounts[] .name' > volume_names
  # Loop through the volume names and create a PV and PVC for each
  create_pv_pvc volume_names
  # Set the SECRETS_CREATOR_API_TOKEN in minikube_configuration.json in wf_engine_config
  jq --arg secrets_creator_api_token "$SECRETS_CREATOR_API_TOKEN" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .wf_engine_config.secrets_creator_api_token = $secrets_creator_api_token else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json
  # Set the SECRETS_CREATOR_API_ENDPOINT in minikube_configuration.json in wf_engine_config
  jq --arg secrets_creator_api_endpoint "$SECRETS_CREATOR_API_ENDPOINT" --arg vl "$VIRTUAL_LAB_NAME" '.vl_configurations |= map(if .name == $vl then .wf_engine_config.secrets_creator_api_endpoint = $secrets_creator_api_endpoint else . end)' minikube_configuration.json > tmp.json && mv tmp.json minikube_configuration.json

  export CONFIG_FILE_URL="minikube_configuration.json"
  cp minikube_configuration.json ../minikube_configuration.json
else
    echo "configuration.json does not exist, skipping update"
fi

# Check if CONFIG_FILE_URL exists
if [ -f "$CONFIG_FILE_URL" ]; then
    echo "Configuration file $CURRENT_DIR/minikube_configuration.json exists."
else
  export CONFIG_FILE_URL="minikube_configuration.json"
fi
# Set $CONFIG_FILE_URL absolute path
CONFIG_FILE_URL=$(realpath "$CONFIG_FILE_URL")
echo "Configuration file URL: $CONFIG_FILE_URL"
cat $CONFIG_FILE_URL
}

test_github_token(){
  # Test CELL_GITHUB_TOKEN with CELL_GITHUB_URL
  echo "Testing CELL_GITHUB_TOKEN with CELL_GITHUB_URL"
  GITHUB_API_PREFIX='https://api.github.com/repos'
  GITHUB_WORKFLOW_FILENAME='build-push-docker.yml'
  # Get the owner and repo from CELL_GITHUB_URL
  OWNER=$(echo "$CELL_GITHUB_URL" | awk -F'/' '{print $(NF-1)}')
  REPO=$(echo "$CELL_GITHUB_URL" | awk -F'/' '{print $NF}' | sed 's/.git$//')
  API="$GITHUB_API_PREFIX/$OWNER/$REPO/actions/workflows/$GITHUB_WORKFLOW_FILENAME"
}

export_variables(){
  # Export environment variables to dev-setup.env
  echo "Exporting environment variables to dev-setup.env"
  {
    echo "AUTH_TOKEN=$AUTH_TOKEN"
    echo "VERIFY_SSL=$VERIFY_SSL"
    echo "DISABLE_AUTH=$DISABLE_AUTH"
    echo "CONFIG_FILE_URL=$CONFIG_FILE_URL"
    echo "DISABLE_OAUTH=False"
    echo "USERNAME=$USERNAME"
    echo "USER_PASSWORD=$USER_PASSWORD"
    echo "KEYCLOAK_AMIN_USER=$KEYCLOAK_AMIN_USER"
    echo "OIDC_CONFIGURATION_URL=$OIDC_CONFIGURATION_URL"
    echo "REGISTRY_TOKEN_FOR_TESTS=$REGISTRY_TOKEN_FOR_TESTS"
    echo "CELL_GITHUB_TOKEN=$CELL_GITHUB_TOKEN"
    echo "ARGO_TOKEN=$ARGO_TOKEN"
    echo "SECRETS_CREATOR_API_ENDPOINT"="$SECRETS_CREATOR_API_ENDPOINT"
    echo "SECRETS_CREATOR_API_TOKEN"="$SECRETS_CREATOR_API_TOKEN"
    echo "USER_EMAIL"="$USER_EMAIL"
  } > dev-setup.env

  # Marge dev-setup.env to dev.env
  if [ -f "dev.env" ]; then
    # If a variable exists in both files, overwrite it with the value from dev-setup.env
    echo "Merging dev-setup.env to dev.env"
    cat dev.env dev-setup.env | sort -u > mergedfile
    mv mergedfile dev-setup.env
  else
    echo "Creating dev.env from dev-setup.env"
    cat dev-setup.env >> dev.env
  fi
}

export_variables_to_github_env() {
  # Write variables from dev-setup.env into GITHUB_ENV for GitHub Actions
  # Skip empty lines and comments, and strip any leading `export ` tokens.
  if [ -z "$GITHUB_ENV" ]; then
    GITHUB_ENV=/dev/stdout
  fi

  if [ ! -f "dev-setup.env" ]; then
    echo "dev-setup.env not found, skipping write to GITHUB_ENV"
    return 0
  fi

  echo "Writing variables from dev-setup.env to GITHUB_ENV ($GITHUB_ENV)"

  # Count variables written
  var_count=0
  while IFS= read -r line || [ -n "$line" ]; do
    # Trim leading/trailing whitespace
    trimmed=$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    # Skip empty or commented lines
    if [ -z "$trimmed" ] || [[ "$trimmed" =~ ^# ]]; then
      continue
    fi
    # Remove leading 'export ' if present
    sanitized=$(echo "$trimmed" | sed -E 's/^export[[:space:]]+//')
    # Append to GITHUB_ENV
    echo "$sanitized" >> "$GITHUB_ENV"
    var_count=$((var_count+1))
  done < dev-setup.env
}

setup_minikube

deploy_naavre

setup_authentication

setup_argo

get_argo_token

setup_configuration_json

get_auth_token

export_variables

export_variables_to_github_env


# Print services urls
echo "NaaVRE services are set up in Minikube. Access them at:"
echo "Workflow Service: https://$MINIKUBE_HOST/workflow-service/"
echo "Containerizer Service: https://$MINIKUBE_HOST/containerizer-service/"
echo "K8s Secret Creator Service: https://$MINIKUBE_HOST/k8s-secret-creator/"
echo "Keycloak Service: https://$MINIKUBE_HOST/auth/"
echo "Argo Workflows UI: https://$MINIKUBE_HOST/argowf/"
echo "Setup complete."