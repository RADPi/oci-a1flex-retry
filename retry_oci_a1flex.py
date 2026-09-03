#!/usr/bin/env python3
"""
Reintenta crear una instancia OCI Ampere A1.Flex (Always Free) hasta que haya
capacidad disponible en la availability domain. Pensado para correr
periodicamente desde GitHub Actions (o cualquier runner con salida a
internet), usando una API signing key de OCI en vez de la consola web.

No requiere ningun OCID hardcodeado salvo tenancy/user/fingerprint (que son
identificadores, no secretos) - el subnet, la image y la availability domain
se resuelven en cada corrida via la API, por nombre.

Variables de entorno requeridas:
  OCI_USER_OCID        OCID del usuario (ocid1.user.oc1..xxxx)
  OCI_FINGERPRINT       Fingerprint de la API key (aa:bb:cc:...)
  OCI_TENANCY_OCID      OCID del tenancy (ocid1.tenancy.oc1..xxxx)
  OCI_REGION             Ej: sa-saopaulo-1
  OCI_PRIVATE_KEY        Contenido COMPLETO del archivo .pem de la API key
                          (incluyendo -----BEGIN/END PRIVATE KEY-----)
  OCI_SSH_PUBLIC_KEY     Clave publica SSH a inyectar en la instancia
  OCI_VCN_NAME           Nombre de tu VCN en OCI
  OCI_SUBNET_NAME        Nombre de tu subnet en OCI
  OCI_INSTANCE_NAME      Nombre que va a tener la instancia a crear

Variables de entorno opcionales:
  OCI_COMPARTMENT_ID    Por defecto: el tenancy (compartment raiz)
  OCI_OS_NAME            Por defecto: "Oracle Linux"
  OCI_OS_VERSION         Por defecto: "9"

Nota: todos los identificadores (VCN, subnet, nombre de instancia) se piden
por variable de entorno a proposito, sin ningun valor por defecto real, para
que el codigo de este repo no contenga ningun dato identificable de una
cuenta OCI en particular - asi se puede tener este repo en un GitHub publico
sin exponer nada mas alla de "existe una automatizacion", que no compromete
la cuenta.

Comportamiento:
  - Intenta crear la instancia con SHAPE_CONFIG (2 OCPU / 12 GB, el maximo
    del pool Always Free de A1.Flex) contra VM.Standard.A1.Flex.
  - Si Oracle devuelve "Out of capacity", no corta ahi: espera
    RETRY_DELAY_SECONDS y reintenta, varias veces dentro de la misma
    corrida, hasta agotar el presupuesto de tiempo MAX_RUN_SECONDS (se deja
    margen frente al timeout-minutes del job de GitHub Actions). Asi cada
    corrida programada hace varios intentos seguidos en vez de uno solo.
  - Si se agota el tiempo sin conseguir capacidad, termina con exit code 0
    (no es un error real) y queda esperando a la proxima corrida programada.
  - Si hay un error inesperado (cuota, permisos, config mal armada),
    termina con exit code 1 para que el run de GitHub Actions se marque
    como fallido y el usuario reciba la notificacion de GitHub.
  - Si logra crear la instancia, escribe outputs para el workflow
    (success=true, instance_id, shape_desc) via $GITHUB_OUTPUT.
"""

import os
import sys
import time

import oci


SHAPE_CONFIG = (2, 12)  # (ocpus, memory_gb) - el maximo del pool Always Free de A1.Flex

RETRY_DELAY_SECONDS = 15  # pausa entre intentos dentro de la misma corrida
MAX_RUN_SECONDS = 240  # tiempo maximo reintentando en esta corrida (deja margen al timeout-minutes del job)


def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        print(f"ERROR: falta la variable de entorno {name}", file=sys.stderr)
        sys.exit(1)
    return val


def write_output(**kwargs):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        for k, v in kwargs.items():
            f.write(f"{k}={v}\n")


def is_capacity_error(e: oci.exceptions.ServiceError) -> bool:
    msg = (e.message or "").lower()
    code = (e.code or "").lower()
    return "capacity" in msg or "capacity" in code


def is_rate_limit_error(e: oci.exceptions.ServiceError) -> bool:
    return e.status == 429 or (e.code or "").lower() in ("toomanyrequests",)


def main() -> int:
    user_ocid = env("OCI_USER_OCID", required=True)
    fingerprint = env("OCI_FINGERPRINT", required=True)
    tenancy_ocid = env("OCI_TENANCY_OCID", required=True)
    region = env("OCI_REGION", required=True)
    private_key = env("OCI_PRIVATE_KEY", required=True)
    ssh_public_key = env("OCI_SSH_PUBLIC_KEY", required=True)

    vcn_name = env("OCI_VCN_NAME", required=True)
    subnet_name = env("OCI_SUBNET_NAME", required=True)
    instance_name = env("OCI_INSTANCE_NAME", required=True)

    compartment_id = env("OCI_COMPARTMENT_ID", default=tenancy_ocid)
    os_name = env("OCI_OS_NAME", default="Oracle Linux")
    os_version = env("OCI_OS_VERSION", default="9")

    config = {
        "user": user_ocid,
        "fingerprint": fingerprint,
        "tenancy": tenancy_ocid,
        "region": region,
        "key_content": private_key,
    }

    identity = oci.identity.IdentityClient(config)
    network = oci.core.VirtualNetworkClient(config)
    compute = oci.core.ComputeClient(config)

    # Ya existe alguna instancia con este nombre? Si ya se creo con exito en
    # una corrida anterior, no insistir de nuevo.
    existing = compute.list_instances(
        compartment_id=compartment_id, display_name=instance_name
    ).data
    active = [i for i in existing if i.lifecycle_state not in ("TERMINATED", "TERMINATING")]
    if active:
        inst = active[0]
        print(f"Ya existe una instancia '{instance_name}' ({inst.id}) en estado {inst.lifecycle_state}. Nada para hacer.")
        write_output(success="true", instance_id=inst.id, shape_desc="ya existente", already_existed="true", instance_name=instance_name)
        return 0

    ads = identity.list_availability_domains(compartment_id=compartment_id).data
    if not ads:
        print("ERROR: no se encontraron availability domains", file=sys.stderr)
        return 1
    availability_domain = ads[0].name
    print(f"Availability domain: {availability_domain}")

    vcns = network.list_vcns(compartment_id=compartment_id, display_name=vcn_name).data
    if not vcns:
        print(f"ERROR: no se encontro la VCN '{vcn_name}'", file=sys.stderr)
        return 1
    vcn_id = vcns[0].id

    subnets = network.list_subnets(
        compartment_id=compartment_id, vcn_id=vcn_id, display_name=subnet_name
    ).data
    if not subnets:
        print(f"ERROR: no se encontro la subnet '{subnet_name}' en la VCN '{vcn_name}'", file=sys.stderr)
        return 1
    subnet_id = subnets[0].id
    print(f"Subnet: {subnet_id}")

    images = compute.list_images(
        compartment_id=compartment_id,
        operating_system=os_name,
        operating_system_version=os_version,
        shape="VM.Standard.A1.Flex",
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        print(f"ERROR: no se encontro una imagen '{os_name} {os_version}' compatible con VM.Standard.A1.Flex", file=sys.stderr)
        return 1
    image = images[0]
    print(f"Imagen: {image.display_name} ({image.id})")

    ocpus, mem_gb = SHAPE_CONFIG
    shape_desc = f"{ocpus} OCPU / {mem_gb} GB"
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        shape="VM.Standard.A1.Flex",
        display_name=instance_name,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus, memory_in_gbs=mem_gb
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id, assign_public_ip=True
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image.id),
        metadata={"ssh_authorized_keys": ssh_public_key},
    )

    start = time.monotonic()
    attempt = 0
    last_error = None
    while True:
        elapsed = time.monotonic() - start
        if elapsed > MAX_RUN_SECONDS:
            print(f"Se agoto el tiempo de esta corrida ({attempt} intento(s) en {int(elapsed)}s). Sigue en la proxima corrida programada.")
            break
        attempt += 1
        print(f"Intento {attempt} (t+{int(elapsed)}s): lanzando instancia con {shape_desc} ...")
        try:
            resp = compute.launch_instance(details)
            instance_id = resp.data.id
            print(f"EXITO: instancia creada ({shape_desc}): {instance_id}")
            print(f"::notice::Instancia '{instance_name}' creada ({shape_desc}): {instance_id}")
            write_output(success="true", instance_id=instance_id, shape_desc=shape_desc, already_existed="false", instance_name=instance_name)
            return 0
        except oci.exceptions.ServiceError as e:
            if is_capacity_error(e):
                print(f"  -> Out of capacity. Reintento en {RETRY_DELAY_SECONDS}s.")
                last_error = e
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            if is_rate_limit_error(e):
                backoff = RETRY_DELAY_SECONDS * 2
                print(f"  -> Rate limited por la API (429). Espero {backoff}s antes de reintentar.")
                last_error = e
                time.sleep(backoff)
                continue
            # Error real (cuota, permisos, config invalida, etc.) -> no tiene sentido seguir reintentando ciegamente
            print(f"ERROR inesperado: status={e.status} code={e.code} message={e.message}", file=sys.stderr)
            return 1

    print(f"Sin capacidad disponible para {shape_desc} en esta corrida ({attempt} intento(s)).")
    if last_error is not None:
        print(f"(ultimo mensaje de OCI: {last_error.message})")
    write_output(success="false")
    return 0  # no es un error del script, solo no hay capacidad todavia


if __name__ == "__main__":
    sys.exit(main())
